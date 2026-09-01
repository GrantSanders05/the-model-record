"""
backtest.py — walk-forward evaluation.

THE RULE THIS FILE EXISTS TO ENFORCE
Games are replayed in strict chronological order. For every game the model
PREDICTS first and only then OBSERVES the result. A rating used to predict
week 8 can only contain information from weeks 1-7.

That ordering is what separates a real backtest from the flattering kind.
It is also the most likely explanation for the 62.1% ATS on record: grading
position groups every two weeks from film that includes the games being
predicted leaks the answer into the input.

Scoring is restricted to `test_seasons`, but the model still walks through
earlier seasons first so it arrives at the test window with real ratings
rather than a cold start.

Usage:
    python3 src/backtest.py --sport cfb --test 2023,2024,2025
    python3 src/backtest.py --sport nfl --test 2020-2025 --rater elo
"""

import argparse
import json
import sqlite3
from collections import defaultdict

import db
import engine
import metrics


def load_rankings(conn, sport):
    """(season, week, team) -> rank, using the AP poll where available."""
    out = {}
    q = """
        SELECT season, week, team, rank, poll FROM rankings
        WHERE sport = ? ORDER BY season, week,
              CASE poll WHEN 'AP Top 25' THEN 0 WHEN 'Coaches Poll' THEN 1 ELSE 2 END
    """
    for r in conn.execute(q, (sport,)):
        key = (r["season"], r["week"], r["team"])
        if key not in out:
            out[key] = r["rank"]
    return out


def rank_asof(ranks, season, week, team):
    """Most recent published rank at or before `week`. Never looks forward."""
    for w in range(week, 0, -1):
        v = ranks.get((season, w, team))
        if v is not None:
            return v
    return None


def load_grades(conn, sport):
    """{(season, team): [(week, {position: grade}), ...]} sorted by week."""
    tmp = defaultdict(lambda: defaultdict(dict))
    for r in conn.execute(
        "SELECT season, week, team, position, grade FROM grades WHERE sport = ?",
        (sport,),
    ):
        tmp[(r["season"], r["team"])][r["week"]][r["position"]] = r["grade"]
    return {k: sorted(v.items()) for k, v in tmp.items()}


def load_games(conn, sport, seasons=None, fbs_only=True, require_line=False):
    """
    Chronologically ordered games with market line and opponent ranks attached.

    Ordering key is (season, week, kickoff, game_id) — deterministic even when
    kickoff timestamps are missing or tied, so a backtest is reproducible.
    """
    where = ["g.sport = ?"]
    params = [sport]
    if seasons:
        where.append("g.season IN (%s)" % ",".join("?" * len(seasons)))
        params.extend(seasons)
    if fbs_only and sport == "cfb":
        # Lines only exist for FBS games; FCS opponents still matter for the
        # loss-quality penalty, so keep games where AT LEAST ONE side is FBS.
        where.append("(g.home_div = 'fbs' OR g.away_div = 'fbs')")

    sql = """
        SELECT g.*, l.home_margin AS market_margin, l.total AS market_total,
               l.home_ml, l.away_ml
        FROM games g
        LEFT JOIN lines l ON l.game_id = g.game_id
        WHERE %s
        ORDER BY g.season, g.week, COALESCE(g.kickoff, ''), g.game_id
    """ % " AND ".join(where)

    ranks = load_rankings(conn, sport)
    out = []
    for r in conn.execute(sql, params):
        g = dict(r)
        if require_line and g.get("market_margin") is None:
            continue
        wk = g["week"] or 1
        g["home_rank"] = rank_asof(ranks, g["season"], wk, g["home_team"])
        g["away_rank"] = rank_asof(ranks, g["season"], wk, g["away_team"])
        out.append(g)
    return out


def run(games, config, test_seasons=None, score_only_with_line=True):
    """
    Replay `games` in order. Returns the list of scored predictions.

    A prediction is recorded only for games in `test_seasons` (all games if
    None), but every game is observed so ratings stay current.
    """
    grades = config.pop("_grades", None) if isinstance(config, dict) else None
    stats = config.pop("_stats", None) if isinstance(config, dict) else None
    model = engine.Model(config, grades, stats)
    preds = []
    season = None
    test = set(test_seasons) if test_seasons else None

    for g in games:
        if g["season"] != season:
            season = g["season"]
            model.new_season(season)

        in_test = test is None or season in test
        # ---- PREDICT BEFORE OBSERVE. Do not reorder these two blocks. ----
        if in_test and g["home_score"] is not None and g["away_score"] is not None:
            p = model.predict(g)
            if not (score_only_with_line and g["market_margin"] is None):
                rec = {
                    "game_id": g["game_id"],
                    "season": g["season"],
                    "week": g["week"],
                    "home_team": g["home_team"],
                    "away_team": g["away_team"],
                    "pred_margin": p["pred_margin"],
                    "market_margin": g["market_margin"],
                    "actual_margin": g["home_score"] - g["away_score"],
                }
                if "pred_total" in p:
                    rec["pred_total"] = p["pred_total"]
                    rec["market_total"] = g["market_total"]
                    rec["actual_total"] = g["home_score"] + g["away_score"]
                preds.append(rec)
        model.observe(g)
        # ------------------------------------------------------------------
    return preds


def parse_seasons(spec):
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--test", default="2023,2024,2025")
    ap.add_argument("--rater", default="elo", choices=["elo", "grades", "blend"])
    ap.add_argument("--config", help="JSON file or inline JSON of config overrides")
    ap.add_argument("--edge", type=float, default=0.0,
                    help="only bet when |model - market| >= this")
    ap.add_argument("--buckets", action="store_true", help="show ATS by edge size")
    args = ap.parse_args()

    conn = db.connect()
    cfg = {"rater": args.rater}
    if args.config:
        raw = open(args.config).read() if args.config.endswith(".json") else args.config
        cfg.update(json.loads(raw))

    test_seasons = parse_seasons(args.test)
    games = load_games(conn, args.sport)
    if not games:
        raise SystemExit("No games for sport=%s. Run the fetcher first." % args.sport)

    cfg["_grades"] = load_grades(conn, args.sport)
    preds = run(games, cfg, test_seasons)

    m = metrics.evaluate(preds, edge_threshold=args.edge)
    print(metrics.format_report(
        m, "%s | rater=%s | test seasons %s" % (args.sport, args.rater, args.test)))

    if args.buckets:
        print("\n  ATS by size of disagreement with the market:")
        print("    %-8s %6s %8s %9s" % ("edge", "n", "ATS%", "ROI%"))
        for b in metrics.edge_buckets(preds):
            print("    %-8s %6d %8s %9s" % (
                b["range"], b["n"],
                "%.2f" % b["ats_pct"] if b["ats_pct"] is not None else "-",
                "%+.2f" % b["roi"] if b["roi"] is not None else "-"))


if __name__ == "__main__":
    main()
