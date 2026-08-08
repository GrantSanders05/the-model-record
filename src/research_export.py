"""
research_export.py — build the JSON bundle the private research app reads.

The research site is static: no database, no server, no API. Everything it
needs is exported here into one file the browser fetches once. That keeps it
free to host, instant to load, and impossible to break with a bad query at 6am
on a Saturday.

It also means the what-if grade editor works entirely client-side. The model's
arithmetic is simple enough to reimplement in JavaScript exactly:

    TEAM TOTAL = 2*(QB+RB+WR+OL+DL+LB+DB) + Coach/ST + WinPts - LossPts
    MARGIN     = home TOTAL - away TOTAL + HFA

so the browser can re-price every one of a team's games the moment a grade is
nudged, with no round trip. The exported `config` block carries the same
constants the Python engine uses, so the two cannot drift apart.
"""

import argparse
import datetime as dt
import json
import os

import backtest
import best_bets
import db
import ledger
import predict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def latest_season_with(conn, table, sport, prefer):
    """
    Newest season at or before `prefer` that actually has rows.

    The upcoming season has no grades or efficiency data until it starts, so
    naively exporting `prefer` yields an empty research app. Falling back keeps
    every view populated with the most recent real data, and the caller records
    which season that was so the UI can say so rather than implying it is current.
    """
    row = conn.execute(
        "SELECT MAX(season) s FROM %s WHERE sport=? AND season<=?" % table,
        (sport, prefer)).fetchone()
    return row["s"] if row and row["s"] else prefer


def team_snapshot(conn, sport, season):
    """Latest grade per team, plus the week each grade came from."""
    out = {}
    for r in conn.execute(
            "SELECT week, team, position, grade FROM grades "
            "WHERE sport=? AND season=? ORDER BY week", (sport, season)):
        t = out.setdefault(r["team"], {"grades": {}, "week": r["week"]})
        t["grades"][r["position"]] = r["grade"]
        t["week"] = max(t["week"], r["week"])
    return out


def efficiency_trend(conn, sport, season):
    """Per-team PPA by week — the objective counterpart to the eye-test grades."""
    out = {}
    for r in conn.execute(
            "SELECT team, week, off_ppa, def_ppa FROM team_game_stats "
            "WHERE sport=? AND season=? ORDER BY week", (sport, season)):
        if r["off_ppa"] is None:
            continue
        out.setdefault(r["team"], []).append({
            "week": r["week"],
            "off": round(r["off_ppa"], 3),
            "def": round(r["def_ppa"], 3) if r["def_ppa"] is not None else None,
        })
    return out


def team_results(conn, sport, season):
    """Every game a team has played, with the model's view of it."""
    out = {}
    for r in conn.execute(
            """SELECT g.game_id, g.week, g.home_team, g.away_team, g.home_score,
                      g.away_score, l.home_margin, l.total
               FROM games g LEFT JOIN lines l ON l.game_id=g.game_id
               WHERE g.sport=? AND g.season=? ORDER BY g.week""", (sport, season)):
        for team, opp, is_home in ((r["home_team"], r["away_team"], True),
                                   (r["away_team"], r["home_team"], False)):
            margin = None
            if r["home_score"] is not None:
                margin = (r["home_score"] - r["away_score"]) * (1 if is_home else -1)
            mkt = r["home_margin"]
            out.setdefault(team, []).append({
                "week": r["week"], "opp": opp, "home": is_home,
                "margin": margin,
                "line": (mkt * (1 if is_home else -1)) if mkt is not None else None,
            })
    return out


def line_movement(conn, sport, season, limit_games=400):
    """Every observation of every line, for the movement view."""
    out = {}
    for r in conn.execute(
            """SELECT h.game_id, h.observed_at, h.home_margin, h.total
               FROM line_history h JOIN games g USING(game_id)
               WHERE g.sport=? AND g.season=? ORDER BY h.observed_at""",
            (sport, season)):
        out.setdefault(r["game_id"], []).append({
            "at": r["observed_at"][:16],
            "margin": r["home_margin"],
            "total": r["total"],
        })
    return {k: v for k, v in list(out.items())[:limit_games]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--season", type=int)
    ap.add_argument("--config", default="config/cfb_grades.json")
    ap.add_argument("--out", default="output/research/data.json")
    ap.add_argument("--bankroll", type=float, default=1000.0)
    args = ap.parse_args()

    cfg_path = args.config if os.path.isabs(args.config) else os.path.join(ROOT, args.config)
    config = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}

    conn = db.connect()
    season = args.season or max(
        r["season"] for r in conn.execute(
            "SELECT DISTINCT season FROM games WHERE sport=?", (args.sport,)))

    bets = best_bets.rank(conn, args.sport, config, season=season, bankroll=args.bankroll)
    disp = best_bets.dispersion_check(bets)
    rec = ledger.record(conn, args.sport)

    grade_season = latest_season_with(conn, "grades", args.sport, season)
    eff_season = latest_season_with(conn, "team_game_stats", args.sport, season)

    bundle = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sport": args.sport,
        "season": season,
        "grade_season": grade_season,
        "efficiency_season": eff_season,
        # The constants the browser needs to re-price a game locally. Exported
        # from the live config so the JS and the Python can never disagree.
        "config": {
            "hfa": config.get("hfa", 3.0),
            "neutral_hfa": config.get("neutral_hfa", 0.0),
            "scale": config.get("scale", 1.0),
            "coach_weight": config.get("sheet_coach_weight", 1.0),
            "loss_sign": config.get("sheet_loss_sign", -1.0),
            "raw_wl": config.get("sheet_raw_wl", 0.0),
        },
        "dispersion": disp,
        "record": {k: v for k, v in rec.items() if k not in ("rows", "curve")},
        "curve": rec.get("curve", []),
        "bets": bets,
        "teams": team_snapshot(conn, args.sport, grade_season),
        "efficiency": efficiency_trend(conn, args.sport, eff_season),
        "results": team_results(conn, args.sport, eff_season),
        "movement": line_movement(conn, args.sport, season),
    }

    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(bundle, fh, separators=(",", ":"))
    kb = os.path.getsize(out) / 1024.0
    print("research bundle -> %s  (%.0f KB)" % (out, kb))
    print("  %d upcoming games | %d teams graded (%d) | %d with efficiency (%d)"
          % (len(bets), len(bundle["teams"]), grade_season,
             len(bundle["efficiency"]), eff_season))
    if grade_season != season:
        print("  NOTE: no %d grades yet — showing %d. Upload the new workbook and sync."
              % (season, grade_season))
    if disp and not disp["ok"]:
        print("  NOTE: dispersion ratio %.2f — the app will show the not-usable banner."
              % disp["ratio"])


if __name__ == "__main__":
    main()
