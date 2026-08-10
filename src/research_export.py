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
import bet_log
import db
import ledger
import predict
import tracking

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def latest_season_with(conn, table, sport, prefer, where=""):
    """
    Newest season at or before `prefer` that actually has USABLE rows.

    The upcoming season has no grades or efficiency data until it starts, so
    naively exporting `prefer` yields an empty research app. Falling back keeps
    every view populated with the most recent real data, and the caller records
    which season that was so the UI can say so rather than implying it is current.

    "Has rows" was too weak a test and it cost the efficiency chart. Once the new
    season's schedule is fetched, team_game_stats holds 2026 rows whose PPA columns
    are all NULL -- games exist, nobody has played one. This picked 2026, found rows,
    and returned a season the trend view could render nothing from, silently dropping
    136 teams of 2025 history. `where` lets the caller say what usable means for its
    own table; the default keeps the old behaviour for tables where existing is enough.

    Returns None when NOTHING matches, rather than falling back to `prefer`. That
    fallback was the dangerous branch and it hid a dead feature: fetch_ppa was never
    called from anywhere, so team_game_stats was empty on every machine except the one
    laptop that had run it by hand, and this dutifully answered "2026" -- a real season
    number, printed as `0 with efficiency (2026)`, which reads like the new season has
    not started rather than like the table is empty. The caller decides the fallback
    and can now say which of the two happened.
    """
    row = conn.execute(
        "SELECT MAX(season) s FROM %s WHERE sport=? AND season<=? %s" % (table, where),
        (sport, prefer)).fetchone()
    return row["s"] if row and row["s"] else None


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


def display_weeks(conn, sport, season, min_gap_days=3):
    """
    {game_id: label} — the week a human would call it.

    CFBD has no week 0. The late-August slate that everyone calls Week 0 is filed
    under week 1, so the site showed one 211-game "week 1" spanning twelve days and
    two distinct weekends. Splitting it back out is a DISPLAY concern only: games.week
    is the walk-forward guard and the key the ledger's locked picks are stamped with,
    so it is never rewritten. Only the label changes.

    The split is found in the calendar rather than hard-coded, because the date moves
    every year: inside week 1, look for a gap of `min_gap_days` or more between
    consecutive kickoff dates. There is exactly one in a real schedule -- the empty
    week between the Week 0 games and Labor Day weekend -- and if there is none (a
    season with no Week 0 at all) nothing is relabelled.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT game_id, kickoff FROM games "
        "WHERE sport=? AND season=? AND week=1 AND kickoff IS NOT NULL "
        "ORDER BY kickoff", (sport, season))]
    if not rows:
        return {}
    days = sorted({r["kickoff"][:10] for r in rows})
    cut = None
    for a, b in zip(days, days[1:]):
        da, db_ = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
        if (db_ - da).days >= min_gap_days:
            cut = b
            break
    labels = {} if not cut else {
        r["game_id"]: 0 for r in rows if r["kickoff"][:10] < cut}
    labels.update(bowl_weeks(conn, sport, season))
    return labels


BOWLS = 99          # sorts after every real week; displayed as "Bowls"


def bowl_weeks(conn, sport, season):
    """
    {game_id: 99} for bowls and playoff games.

    CFBD numbers the postseason from 1, so a bowl on 27 December is week 1 — the
    same key as a game on the opening Saturday of the season. That is not a display
    nuisance, it is an ambiguity: a bet logged as "week 1, San Diego State" matched
    the September game instead of the bowl, and the fixture caught it doing so with
    a 27-point closing line value nobody would have believed.

    December is the honest boundary. Bowl season is always December and January,
    every year, so the rule holds without hard-coding a date; the November games
    CFBD also files as postseason are conference championship and rivalry weekends
    that genuinely belong to the week number they carry, and they keep it.
    """
    return {r["game_id"]: BOWLS for r in conn.execute(
        "SELECT game_id FROM games WHERE sport=? AND season=? AND season_type='postseason' "
        "AND kickoff IS NOT NULL AND (substr(kickoff,6,2) IN ('12','01'))",
        (sport, season))}


def schedule(conn, sport, season, priced, labels, rated=(), keep=()):
    """
    Every game of the season, not only the ones worth betting.

    The bets board is deliberately narrow: it drops games the ratings cannot reach,
    because a 45-point line produces a huge fake EV that would otherwise top the
    board. That makes it the wrong place to answer "who plays this week", which is a
    different question and needs the opposite treatment -- show everything, and be
    explicit about which games the model is not pricing and why.

    "Everything" still means everything in THIS model's world. CFBD's schedule
    carries all divisions, and 750 of the season's 1,638 games are FCS or Division II
    fixtures with no FBS team in them -- Ohio Dominican at Morehead State. They are
    not games Grant is missing; they are games he has no rating for and never will.
    Keeping them would double the bundle and bury the 888 that matter. A game with
    ONE FBS team is kept: he does have a view on those.

    `keep` OVERRIDES THE FILTER, and it exists to hold an invariant the browser now
    depends on: EVERY GAME ANY LOGGED BET REFERS TO IS IN THIS LIST. Bets are graded
    in the browser against this schedule, so a bet on a game the filter dropped would
    render as permanently ungraded with no explanation -- a real result silently
    replaced by a blank. Nothing about the rating filter is a statement that a game
    cannot be bet.
    """
    rated = set(rated)
    keep = {str(k) for k in keep}
    out = []
    for r in conn.execute(
            """SELECT g.game_id, g.week, g.kickoff, g.home_team, g.away_team,
                      g.home_score, g.away_score, g.neutral_site,
                      l.home_margin, l.total, l.home_ml, l.away_ml
               FROM games g LEFT JOIN lines l ON l.game_id=g.game_id
               WHERE g.sport=? AND g.season=? ORDER BY g.kickoff, g.game_id""",
            (sport, season)):
        if (rated and r["home_team"] not in rated and r["away_team"] not in rated
                and str(r["game_id"]) not in keep):
            continue
        p = priced.get(r["game_id"])
        played = r["home_score"] is not None
        rec = {
            "game_id": r["game_id"],
            "week": labels.get(r["game_id"], r["week"]),
            "cfbd_week": r["week"],
            "kickoff": r["kickoff"],
            "away": r["away_team"], "home": r["home_team"],
            "neutral": bool(r["neutral_site"]),
            "line": r["home_margin"], "total": r["total"],
            # Carried so the browser's bet form can fill in the price on offer
            # instead of asking him to retype a number that is already on screen.
            # A default that is nearly always right is the difference between a
            # log he keeps and a log he abandons.
            "home_ml": r["home_ml"], "away_ml": r["away_ml"],
            "model_margin": p["model_margin"] if p else None,
            "model_total": p["model_total"] if p else None,
            "edge": p["spread_edge"] if p else None,
            "ats_pick": p["ats_pick"] if p else None,
            "ou_pick": p["ou_pick"] if p else None,
            "ml_pick": p["ml_pick"] if p else None,
            "ml_ev": p["ml_ev"] if p else None,
            "no_bet": bool(p["no_bet"]) if p else False,
            "home_score": r["home_score"], "away_score": r["away_score"],
        }
        if played:
            rec["result_margin"] = r["home_score"] - r["away_score"]
        # Why a game carries no model number, in the game's own row. "—" with no
        # reason reads as a bug; these are three different situations and only one
        # of them is a gap in the data.
        rec["status"] = ("final" if played else
                         "blowout" if rec["no_bet"] else
                         "priced" if p else "unrated")
        out.append(rec)
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


def load_json(rel):
    """Optional sidecar file, or None. Absent is a normal state, not an error."""
    p = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p))
    except ValueError:
        return None


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


def build_my_bets(conn, sport, season, labels):
    """
    The sheet-sourced half of his bet log, or a description of why there isn't one.

    Bets are typed on the website now and live in the browser; this path exists for
    the rows already in the old `My Bets` tab. Absent tab, unreachable sheet and a
    tab full of typos are three different states and the app says which — none of
    them may stop the export, because the picks have nothing to do with the sheet.
    """
    raw = load_json("output/my_bets_raw.json")
    if raw is None:
        return {"state": "never fetched"}
    if raw.get("missing"):
        return {"state": "no tab", "fetched_utc": raw.get("fetched_utc"),
                "tabs_seen": raw.get("tabs_seen", [])}
    try:
        built = bet_log.build(conn, sport, season, raw.get("rows") or [],
                              week_labels=labels)
        built["state"] = "ok"
        built["fetched_utc"] = raw.get("fetched_utc")
        return built
    except Exception as e:                         # noqa: BLE001 - report anything
        print("  WARNING: could not build the bet log — %s: %s" % (type(e).__name__, e))
        return {"state": "error", "error": "%s: %s" % (type(e).__name__, e)}


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

    # Labels first: the bets board and the schedule must agree about what week a game
    # is in, or the same fixture appears under two different weeks on two tabs.
    labels = display_weeks(conn, args.sport, season)
    priced = {b["game_id"]: b for b in bets}
    grade_season_for_teams = latest_season_with(conn, "grades", args.sport, season)
    rated = [r["team"] for r in conn.execute(
        "SELECT DISTINCT team FROM grades WHERE sport=? AND season=?",
        (args.sport, grade_season_for_teams))]

    # The bet log is built BEFORE the schedule, because the schedule has to contain
    # every game it refers to. The browser grades bets against the exported schedule
    # now, so a bet on a game the rating filter would have dropped has to keep its
    # fixture or it renders as ungraded forever.
    mybets = build_my_bets(conn, args.sport, season, labels)
    slate = schedule(conn, args.sport, season, priced, labels, rated=rated,
                     keep=[b["game_id"] for b in mybets.get("bets", [])])
    for b in bets:
        b["week"] = labels.get(b["game_id"], b["week"])

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

    grade_season = latest_season_with(conn, "grades", args.sport, season) or season
    eff_found = latest_season_with(conn, "team_game_stats", args.sport, season,
                                   where="AND off_ppa IS NOT NULL")
    eff_season = eff_found or season

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
        "schedule": slate,
        "week0": bool(labels),
        "grades_sync": load_json("output/grades_sync.json"),
        # The model's own record, cut every way that could change the answer, and
        # the bets Grant actually placed. Two different records on purpose — see
        # the header of bet_log.py for why conflating them is the classic mistake.
        # `mybets` now carries only the rows still coming from the Google Sheet; the
        # bets he types on the site live in his browser and never reach this file.
        "tracking": tracking.summary(conn, args.sport, season=None, week_labels=labels),
        "mybets": mybets,
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
    print("  %d upcoming games | %d teams graded (%d) | %d with efficiency (%s)"
          % (len(bets), len(bundle["teams"]), grade_season,
             len(bundle["efficiency"]),
             eff_season if eff_found else "NO PPA DATA AT ALL"))
    if not eff_found:
        print("  WARNING: team_game_stats holds no usable PPA for any season, so the")
        print("  efficiency view will be empty for every team. Run fetch_cfb.py to load it.")
    if grade_season != season:
        print("  NOTE: no %d grades yet — showing %d. Upload the new workbook and sync."
              % (season, grade_season))
    if disp and not disp["ok"]:
        print("  NOTE: dispersion ratio %.2f — the app will show the not-usable banner."
              % disp["ratio"])


if __name__ == "__main__":
    main()
