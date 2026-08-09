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


SEVEN = ["qb", "rb", "wr", "ol", "dl", "lb", "db"]


def conferences(season):
    """
    {team: conference} for the season's alignment.

    Reads the same cached CFBD file the workbook builder uses, so the sheet and
    the site cannot disagree about who plays where -- which matters in a year
    when a third of the country moved.

    THE CACHE IS NOT GUARANTEED TO EXIST. It is written by build_season_sheet.py,
    which runs on Grant's machine and never in CI, so on a fresh runner the file
    is simply absent. Returning {} there was silently wrong in the worst way: every
    team fell back to "Independent", the site showed ONE conference containing all
    138 teams, and nothing errored. So the fetch is done here when the cache is
    missing, and a genuine failure is reported rather than flattened.
    """
    path = os.path.join(ROOT, "data", "cache", "teams-fbs_year-%d.json" % season)
    data = None
    if os.path.exists(path):
        try:
            data = json.load(open(path))
        except ValueError:
            data = None
    if not data:
        try:
            import build_season_sheet
            import fetch_cfb
            data = build_season_sheet.fbs_teams(season, fetch_cfb.load_key())
        except Exception as e:                     # noqa: BLE001 - report anything
            print("  WARNING: no conference data (%s). The rankings view will "
                  "show a single conference." % e)
            return {}

    import team_aliases
    out = {}
    for t in data:
        name = team_aliases.canonical("cfb", t.get("school") or "")
        if name:
            out[name] = t.get("conference") or "Independent"
    return out


def team_snapshot(conn, sport, season):
    """Latest grade per team, with its conference, TOTAL and national rank."""
    out = {}
    for r in conn.execute(
            "SELECT week, team, position, grade FROM grades "
            "WHERE sport=? AND season=? ORDER BY week", (sport, season)):
        t = out.setdefault(r["team"], {"grades": {}, "week": r["week"]})
        t["grades"][r["position"]] = r["grade"]
        t["week"] = max(t["week"], r["week"])

    conf = conferences(season)
    for team, blob in out.items():
        g = blob["grades"]
        blob["conference"] = conf.get(team, "Independent")
        # The sheet's own TOTAL, recomputed here so the rankings table and the
        # spreadsheet agree without the browser having to know the formula.
        if all(p in g for p in SEVEN):
            blob["total"] = round(
                2 * sum(g[p] for p in SEVEN) + g.get("coach_st", 0.0)
                + g.get("_win_points", 0.0) - g.get("_loss_points", 0.0), 1)
        else:
            blob["total"] = None

    ranked = sorted((t for t in out if out[t]["total"] is not None),
                    key=lambda t: -out[t]["total"])
    for i, team in enumerate(ranked, start=1):
        out[team]["rank"] = i
    # Rank within the conference too — "3rd in the Big Ten" is the question
    # actually being asked when a conference filter is on.
    by_conf = {}
    for team in ranked:
        c = out[team]["conference"]
        by_conf.setdefault(c, []).append(team)
    for c, teams in by_conf.items():
        for i, team in enumerate(teams, start=1):
            out[team]["conf_rank"] = i
            out[team]["conf_size"] = len(teams)
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


def load_alerts(path="output/alerts.json"):
    """
    Roster news from `roster_watch.py`, if it has run.

    Kept optional and separate: the alerts job hits ESPN 138 times and should
    never be able to hold up the picks. If it has not run, the site says so
    rather than showing an empty list that reads as "no injuries".
    """
    p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p))
    except ValueError:
        return None
    return {"generated_utc": d.get("generated_utc"),
            "min_points": d.get("min_points"),
            "alerts": d.get("alerts", []),
            "watchlist": d.get("watchlist", [])[:120]}


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

    # Games the ratings cannot reach are dropped from the board rather than
    # shipped with a flag: they carry the largest EV numbers on the page, and a
    # badge is not enough to stop a number that big from being read as the best
    # bet available.
    n_all = len(bets)
    bets = [b for b in bets if not b.get("no_bet")]

    # Until a game is played the quality-points half of the formula is zero, so
    # every rating is a preseason estimate. The site says so rather than
    # presenting week-1 numbers as if they carried the model's measured edge.
    preseason = conn.execute(
        "SELECT COUNT(*) n FROM games WHERE sport=? AND season=? AND home_score IS NOT NULL",
        (args.sport, season)).fetchone()["n"] == 0

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
        "preseason": preseason,
        "alerts": load_alerts(),
        "excluded_blowouts": n_all - len(bets),
        "blowout_line": best_bets.BLOWOUT_LINE,
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
