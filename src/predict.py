"""
predict.py — generate picks for upcoming games.

Walks every completed game in the database in order to bring team ratings
up to the present, then predicts each game that has not been played yet.

Output is a picks table with the model number, the market number, the
disagreement (the "edge"), and a recommended side — plus a total.

The `--min-edge` filter matters more than it looks. Backtesting showed the
model's ATS record is WORSE on its biggest disagreements with the market than
on its smallest ones, which is the signature of miscalibration rather than
insight. Until a rater demonstrably beats the close out-of-sample, treat large
edges as a reason for suspicion, not confidence.

Usage:
    python3 src/predict.py --sport cfb --config config/cfb_elo.json
    python3 src/predict.py --sport cfb --week 3 --min-edge 3 --out picks.csv
"""

import argparse
import csv
import json
import os
import sys

import backtest
import db
import engine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def american_to_prob(ml):
    if ml is None:
        return None
    ml = float(ml)
    return (-ml) / (-ml + 100.0) if ml < 0 else 100.0 / (ml + 100.0)


def margin_to_win_prob(margin, sport):
    """
    Convert a predicted margin to a straight-up win probability using a
    logistic fit whose scale reflects each sport's scoring variance.
    CFB margins are far more dispersed than NFL, so the curve is flatter.
    """
    sigma = {"cfb": 16.5, "nfl": 13.2, "nba": 12.0}.get(sport, 14.0)
    import math
    return 1.0 / (1.0 + math.exp(-margin / (sigma * 0.5513)))


def generate(conn, sport, config, week=None, season=None):
    games = backtest.load_games(conn, sport)
    if not games:
        sys.exit("No games for sport=%s. Run the fetcher first." % sport)

    grades = backtest.load_grades(conn, sport)
    cfg = dict(config)
    cfg.pop("_grades", None)
    model = engine.Model(cfg, grades)

    current_season = season or max(g["season"] for g in games)
    picks = []
    seen_season = None

    for g in games:
        if g["season"] != seen_season:
            seen_season = g["season"]
            model.new_season(seen_season)

        played = g["home_score"] is not None and g["away_score"] is not None
        if not played and g["season"] == current_season:
            if week is not None and g["week"] != week:
                continue
            p = model.predict(g)
            mkt = g["market_margin"]
            edge = (p["pred_margin"] - mkt) if mkt is not None else None
            wp = margin_to_win_prob(p["pred_margin"], sport)
            picks.append({
                "game_id": g["game_id"],
                "season": g["season"],
                "week": g["week"],
                "kickoff": g["kickoff"],
                "away": g["away_team"],
                "home": g["home_team"],
                # Whether the FILM GRADES actually answered this game, or Elo
                # did because a team has no grade. Carried through to the board,
                # which declines to offer a bet the grade model did not make --
                # see best_bets.rank.
                "unrated": bool(p.get("borrowed")),
                "model_margin": round(p["pred_margin"], 1),
                "market_margin": mkt,
                "edge": round(edge, 1) if edge is not None else None,
                "ats_pick": (None if edge is None or edge == 0
                             else (g["home_team"] if edge > 0 else g["away_team"])),
                # STRAIGHT-UP winner, which is not the same thing as the moneyline
                # bet on the Best Bets board -- that one is the +EV side and is
                # often the underdog. This is "who wins", and it is recorded so the
                # ledger can report a straight-up accuracy that needs no odds.
                "ml_pick": g["home_team"] if p["pred_margin"] > 0 else g["away_team"],
                "ml_win_prob": round(100 * max(wp, 1 - wp), 1),
                # ...but the price is carried anyway, because with it the same
                # column also yields a moneyline ROI, and without it a hit rate on
                # favourites is a number that cannot be interpreted at all.
                "ml_odds": (g.get("home_ml") if p["pred_margin"] > 0
                            else g.get("away_ml")),
                "model_total": round(p.get("pred_total"), 1) if p.get("pred_total") else None,
                "market_total": g["market_total"],
                "ou_pick": None,
            })
        model.observe(g)

    for p in picks:
        if p["model_total"] is not None and p["market_total"] is not None:
            p["ou_pick"] = "OVER" if p["model_total"] > p["market_total"] else "UNDER"
    return picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--config", default="config/cfb_elo.json")
    ap.add_argument("--week", type=int)
    ap.add_argument("--season", type=int)
    ap.add_argument("--min-edge", type=float, default=0.0)
    ap.add_argument("--out", help="write picks to this CSV path")
    args = ap.parse_args()

    path = args.config if os.path.isabs(args.config) else os.path.join(ROOT, args.config)
    config = json.load(open(path)) if os.path.exists(path) else {}
    if not config:
        print("NOTE: no config at %s — using engine defaults (un-optimized)." % path)

    conn = db.connect()
    picks = generate(conn, args.sport, config, args.week, args.season)
    picks = [p for p in picks
             if p["edge"] is None or abs(p["edge"]) >= args.min_edge]

    if not picks:
        print("No upcoming games found for sport=%s%s.\n"
              "Off-season, or the schedule hasn't been fetched yet — run:\n"
              "    python3 src/fetch_%s.py --seasons <year> --refresh"
              % (args.sport, " week %s" % args.week if args.week else "", args.sport))
        return

    hdr = ("%-4s %-24s %-24s %7s %7s %7s  %-20s %7s %7s %-6s"
           % ("wk", "away", "home", "model", "market", "edge", "ATS pick", "mTot", "mkTot", "O/U"))
    print(hdr)
    print("-" * len(hdr))
    for p in sorted(picks, key=lambda r: (-abs(r["edge"] or 0))):
        print("%-4s %-24s %-24s %7s %7s %7s  %-20s %7s %7s %-6s" % (
            p["week"], p["away"][:24], p["home"][:24],
            "%+.1f" % p["model_margin"],
            "%+.1f" % p["market_margin"] if p["market_margin"] is not None else "-",
            "%+.1f" % p["edge"] if p["edge"] is not None else "-",
            (p["ats_pick"] or "-")[:20],
            "%.1f" % p["model_total"] if p["model_total"] else "-",
            "%.1f" % p["market_total"] if p["market_total"] else "-",
            p["ou_pick"] or "-"))
    print("\n%d games. Margins are HOME-relative (+ = home favored)." % len(picks))

    if args.out:
        out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(picks[0].keys()))
            w.writeheader()
            w.writerows(picks)
        print("picks written -> %s" % out)


if __name__ == "__main__":
    main()
