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
import engine
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


def football_week_of(day):
    """
    The Tuesday that starts the football week containing `day`.

    College football weeks run Tuesday to Monday: a Thursday opener, a Saturday
    slate and a Monday night game all belong to the same week. Bucketing on the
    preceding Tuesday is therefore the calendar the sport actually uses, and unlike
    a gap it does not move when a game is added.
    """
    d = dt.date.fromisoformat(str(day)[:10])
    return d - dt.timedelta(days=(d.weekday() - 1) % 7)


def display_weeks(conn, sport, season):
    """
    {game_id: label} — the week a human would call it.

    CFBD has no week 0. The late-August slate everyone calls Week 0 is filed under
    week 1, so the site showed one 211-game "week 1" spanning twelve days and two
    distinct weekends. Splitting it back out is a DISPLAY concern only: games.week is
    the walk-forward guard and the key the ledger stamps locked picks with, so it is
    never rewritten. Only the label changes.

    THIS USED TO LOOK FOR A THREE-DAY GAP and it broke the moment the season began.
    The 2026 schedule opened 27-30 Aug, then rested until 3 Sep -- a four-day hole
    the rule found easily in August. Once games started moving, something landed in
    the hole, the gap fell under three days, and the split silently stopped
    happening: week 0 went to zero games, the schedule tab showed one enormous week,
    and the QA suite failed on an assertion about the DATA rather than the code, in
    the workflow nobody was watching. A rule that depends on an absence is a rule
    that any addition can break.

    Bucketing by football week has no such dependency. Group week 1's games by the
    Tuesday that starts their week; if that yields two buckets, the earlier one is
    Week 0. The guard is that Week 0 must be the SMALLER slate -- a genuine week 0 is
    a handful of early games ahead of a full opening weekend, and if the earlier
    bucket is the larger one then this is an ordinary week that happens to straddle
    a Tuesday and must be left alone.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT game_id, kickoff FROM games "
        "WHERE sport=? AND season=? AND week=1 AND kickoff IS NOT NULL "
        "ORDER BY kickoff", (sport, season))]
    labels = {}
    if rows:
        buckets = {}
        for r in rows:
            buckets.setdefault(football_week_of(r["kickoff"]), []).append(r["game_id"])
        ordered = sorted(buckets)
        if len(ordered) >= 2:
            first, rest = buckets[ordered[0]], sum(len(buckets[k]) for k in ordered[1:])
            if len(first) < rest:
                labels = {gid: 0 for gid in first}
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
        played = db.is_final(r)
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


def season_rater(conn, sport, season, config):
    """
    The rating engine, wound forward through every completed game up to `season`.

    Same class, same games, same order as `predict.generate` — so a number read
    off this object IS the number the model is betting, rather than a second
    implementation of the same arithmetic that agrees until it doesn't.

    That second implementation is what this replaces. The team page used to
    total a team from four spreadsheet columns (`Wins`, `Losses`, `Win Points`,
    `Loss Points`) that are hand-entered and empty for 2026 — so it showed bare
    position grades while the model bet on quality points accrued from real
    results, and the footnote underneath told the reader they were the same
    number.
    """
    games = backtest.load_games(conn, sport)
    grades = backtest.load_grades(conn, sport)
    if not grades:
        return None
    cfg = dict(config)
    cfg.pop("_grades", None)
    model = engine.Model(cfg, grades)

    seen = None
    for g in games:
        if g["season"] > season:
            break
        if g["season"] != seen:
            seen = g["season"]
            model.new_season(seen)
        model.observe(g)
    return model


def grade_rater(model):
    """The GradeRater inside whichever rater is configured, or None."""
    if model is None:
        return None
    r = model.rater
    if isinstance(r, engine.GradeRater):
        return r
    return getattr(r, "grades", None)      # BlendRater keeps one alongside Elo


def team_snapshot(conn, sport, season, model=None, week=None):
    """Latest grade per team, with its conference, record, TOTAL and rank."""
    out = {}
    for r in conn.execute(
            "SELECT week, team, position, grade FROM grades "
            "WHERE sport=? AND season=? ORDER BY week", (sport, season)):
        t = out.setdefault(r["team"], {"grades": {}, "week": r["week"]})
        t["grades"][r["position"]] = r["grade"]
        t["week"] = max(t["week"], r["week"])

    rater = grade_rater(model)
    conf = conferences(season)
    for team, blob in out.items():
        g = blob["grades"]
        blob["conference"] = conf.get(team, "Independent")

        # This season's record and the quality points it earned, from the games
        # actually played. Zeroes rather than blanks: 0-0 is a fact about a team
        # that has not played, and it is the honest thing to print in week one.
        blob["record"] = (rater.record(team) if rater
                          else {"wins": 0, "losses": 0, "win_points": 0.0,
                                "loss_points": 0.0, "quality": 0.0})

        # THE TOTAL IS THE MODEL'S OWN. Asking the rater for it, rather than
        # re-deriving the formula here, is what makes "the sheet and the picks
        # are the same number" true by construction instead of by comment.
        total = None
        if rater is not None:
            total = rater._grade_total(season, team, week or blob["week"])
        if total is None and all(p in g for p in SEVEN):
            # No rater (or no grade snapshot at that week) — fall back to the
            # sheet arithmetic so the page still ranks, and say so on the page.
            total = (2 * sum(g[p] for p in SEVEN) + g.get("coach_st", 0.0)
                     + g.get("_win_points", 0.0) - g.get("_loss_points", 0.0))
        blob["total"] = round(total, 1) if total is not None else None

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
            if db.is_final(r):
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


def health(conn, sport, season):
    """
    Whether the pipeline is actually keeping up. Absence of news is not good news.

    The 2026 season opened with every scheduled update failing and the half-hourly
    refresh republishing the same fortnight-old bundle over the top. The site looked
    completely normal for ten days. Nothing on the page could have told anyone
    otherwise, because everything it showed was internally consistent — just old.

    So the page now carries the two numbers that cannot look healthy while the
    pipeline is broken: games that finished and have not been graded, and games that
    kicked off with no pick at all. Both are zero when things are working.
    """
    import ledger
    ungraded = conn.execute(
        "SELECT COUNT(*) c FROM picks_log p JOIN games g USING(game_id) "
        "WHERE p.sport=? AND p.graded_at IS NULL AND p.voided_at IS NULL AND "
        + db.FINAL_SQL.replace("home_score", "g.home_score")
                      .replace("away_score", "g.away_score"),
        (sport,)).fetchone()["c"]
    missed, _ = ledger.missed_locks(conn, sport, season)
    # The third number that cannot look healthy while the pipeline is broken: a
    # pick that exists but was locked weeks early, which no "is anything missing"
    # check can see. See ledger.stale_locks.
    stale, _ = ledger.stale_locks(conn, sport, season)
    last = conn.execute(
        "SELECT MAX(kickoff) k FROM games WHERE sport=? AND season=? AND " + db.FINAL_SQL,
        (sport, season)).fetchone()["k"]
    return {"ungraded_finals": ungraded, "missed_locks": missed,
            "stale_locks": stale, "last_final_kickoff": last}


def _availability_block(conn, sport, season):
    """
    §22 in shadow: what has been observed, and whether the market moved after.

    Exported so the layer is VISIBLE. A table that is written every run and read
    by nothing is the mirror image of the reader-with-no-writer defect, and just
    as quiet — the observations would accumulate for a season and nobody would
    notice they had stopped.
    """
    try:
        import availability as _av
    except Exception as e:                         # noqa: BLE001
        return {"available": False, "why": "%s: %s" % (type(e).__name__, e)}
    try:
        total = conn.execute(
            "SELECT COUNT(*) c FROM availability_events WHERE sport=? AND season=?",
            (sport, season)).fetchone()["c"]
        by_status = {r["status"]: r["c"] for r in conn.execute(
            "SELECT status, COUNT(*) c FROM availability_events"
            " WHERE sport=? AND season=? GROUP BY status", (sport, season))}
        latest = conn.execute(
            "SELECT MAX(observed_at) m FROM availability_events"
            " WHERE sport=? AND season=?", (sport, season)).fetchone()["m"]
        moves = _av.line_movement_after(conn, sport, season)
    except Exception as e:                         # noqa: BLE001
        return {"available": False, "why": "%s: %s" % (type(e).__name__, e)}
    return {
        "available": True,
        "observations": total,
        "by_status": by_status,
        "latest_observed_at": latest,
        "source_tiers": {str(k): v for k, v in _av.TIERS.items()},
        "auto_adjust_eligible_tiers": list(_av.AUTO_ADJUST_ELIGIBLE),
        "adjusts_model": False,
        "why_not": "no calibrated P(absent | status, source, timing) exists yet; "
                   "§22.3 forbids turning Questionable into Out, and wiring "
                   "DEGRADED_AVAILABILITY into the decision rule is a strategy "
                   "version change, not a data-module switch",
        "line_movement": moves[:60],
        "line_movement_n": len(moves),
    }


def _weather_block(conn, sport, season):
    """§23 in shadow: what has been snapshotted, and the gap that remains."""
    try:
        n = conn.execute(
            "SELECT COUNT(*) c FROM weather_snapshots w JOIN games g"
            "   ON g.game_id = w.game_id"
            " WHERE w.sport=? AND g.season=?", (sport, season)).fetchone()["c"]
        latest = conn.execute(
            "SELECT MAX(observed_at) m FROM weather_snapshots WHERE sport=?",
            (sport,)).fetchone()["m"]
        wind = conn.execute(
            "SELECT COUNT(*) c FROM weather_snapshots"
            " WHERE sport=? AND wind_mph IS NOT NULL", (sport,)).fetchone()["c"]
        domes = conn.execute(
            "SELECT COUNT(DISTINCT game_id) c FROM weather_snapshots"
            " WHERE sport=? AND indoor=1", (sport,)).fetchone()["c"]
    except Exception as e:                         # noqa: BLE001
        return {"available": False, "why": "%s: %s" % (type(e).__name__, e)}
    return {
        "available": True,
        "snapshots": n,
        "latest_observed_at": latest,
        "with_wind": wind,
        "indoor_games": domes,
        "source": "espn_scoreboard",
        "adjusts_model": False,
        "gap": "this source publishes no wind, which is the variable §23 names "
               "as the one most likely to matter. The columns exist and stay "
               "empty rather than holding a proxy.",
    }


def build_v2_block(conn, sport, season):
    """
    The V2 record: four scoreboards kept apart, and the challenger comparison.

    Returns a dict with `scoreboards`, `strategies` and `models`. Nothing here is
    summed into a headline — a challenger sorted to the top of a table by ATS
    percentage is exactly the presentation §19.6 warns about, so the table is
    ordered by version and carries the PAIRED comparison instead.
    """
    try:
        import forecast_v2
        import metrics_v2
        import signals as _sig
        import horizons as _hz
    except Exception as e:                         # noqa: BLE001 - never block a build
        return {"available": False, "why": "%s: %s" % (type(e).__name__, e)}

    # A RETIRED champion is not the champion. Duplicates minted by the old
    # calendar-stamped version string are retired rather than deleted, so the
    # filter matters: without it the newest row wins and the page names a version
    # nothing files under any more.
    champ = conn.execute(
        "SELECT model_version FROM model_registry WHERE role='champion'"
        " AND retired_at IS NULL ORDER BY created_at LIMIT 1").fetchone()
    champ_v = champ["model_version"] if champ else None

    strategies = []
    for r in conn.execute(
            "SELECT DISTINCT strategy_version FROM signal_log WHERE is_official=1"
            " ORDER BY strategy_version"):
        sv = r["strategy_version"]
        perf = metrics_v2.signal_performance(conn, strategy_version=sv, sport=sport)
        diag = metrics_v2.closing_diagnostic(conn, strategy_version=sv, sport=sport)
        strategies.append({"strategy_version": sv, "signals": perf, "close": diag})

    models = []
    for r in conn.execute(
            "SELECT model_version, model_id, role, experiment_id, config_hash,"
            "       git_sha, feature_schema_version, created_at, retired_at, notes"
            "  FROM model_registry ORDER BY role, model_version"):
        m = dict(r)
        m["quality"] = metrics_v2.forecast_quality(
            conn, model_version=m["model_version"], sport=sport, season=season)
        if champ_v and m["model_version"] != champ_v:
            m["vs_champion"] = metrics_v2.paired_comparison(
                conn, champion_version=champ_v,
                challenger_version=m["model_version"], sport=sport)
        models.append(m)

    return {
        "available": True,
        "champion": champ_v,
        "official_horizon": _hz.OFFICIAL_HORIZON,
        "strategy_version": _sig.STRATEGY_V0["strategy_version"],
        "strategy_config": _sig.STRATEGY_V0,
        "strategy_hash": _sig.strategy_hash(),
        "declined_reasons": _sig.reason_counts(conn),
        "scoreboards": metrics_v2.all_scoreboards(
            conn, model_version=champ_v,
            strategy_version=_sig.STRATEGY_V0["strategy_version"], sport=sport),
        "strategies": strategies,
        "models": models,
        "availability": _availability_block(conn, sport, season),
        "weather": _weather_block(conn, sport, season),
        "note": "four scoreboards, deliberately not summed: forecast quality is "
                "over every forecast, signal performance only over what the "
                "strategy offered, the close is a diagnostic, and user bets are "
                "not modelled here at all.",
    }


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
    mybets_full = build_my_bets(conn, args.sport, season, labels)
    # The game ids are derived BEFORE the rows are dropped: keeping a bet's game
    # on the schedule is the behaviour, and publishing the bet is not.
    _bet_games = [b["game_id"] for b in (mybets_full.get("bets") or [])]
    # STATUS ONLY, ALWAYS. build_my_bets reads the sheet's My Bets tab; it is
    # empty today, and a bet logged there would otherwise publish with its stake,
    # its book and its ROI into a bundle that has been served in the clear since
    # 1 September. The grades in this bundle are public BY DECISION; a person's
    # wagering is not, and unlike a grade it cannot be un-published.
    import public_export
    mybets = public_export.safe_my_bets(mybets_full)
    slate = schedule(conn, args.sport, season, priced, labels, rated=rated,
                     keep=_bet_games)
    for b in bets:
        b["week"] = labels.get(b["game_id"], b["week"])

    # Games the ratings cannot reach are dropped from the board rather than
    # shipped with a flag: they carry the largest EV numbers on the page, and a
    # badge is not enough to stop a number that big from being read as the best
    # bet available.
    n_all = len(bets)
    # COUNTED BY REASON, because they are two different problems and a reader who
    # is told "the line is wider than 28" about a game the film never graded has
    # been told something false. The first is the grade sheet failing to span a
    # 45-point spread; the second is a team with no grade at all, where Elo
    # answered and holds every non-FBS side at one constant.
    excluded = {"blowout": sum(1 for b in bets if b.get("no_bet_reason") == "blowout"),
                "unrated": sum(1 for b in bets if b.get("no_bet_reason") == "unrated")}
    bets = [b for b in bets if not b.get("no_bet")]

    # Until a game is played the quality-points half of the formula is zero, so
    # every rating is a preseason estimate. The site says so rather than
    # presenting week-1 numbers as if they carried the model's measured edge.
    preseason = conn.execute(
        "SELECT COUNT(*) n FROM games WHERE sport=? AND season=? AND " + db.FINAL_SQL,
        (args.sport, season)).fetchone()["n"] == 0

    grade_season = latest_season_with(conn, "grades", args.sport, season) or season

    # Wound forward through this season's completed games, so the team page can
    # show each team's record, the quality points those results earned, and a
    # TOTAL that is the model's own number rather than a copy of its formula.
    rater_model = season_rater(conn, args.sport, season, config)
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
            # The share of the model's disagreement with the market that history
            # says actually shows up. The browser needs it to render the honest
            # "worth" of an edge rather than the raw gap.
            "edge_realised": config.get("edge_realised", 1.0),
            "coach_weight": config.get("sheet_coach_weight", 1.0),
            # WHICH formula, so the browser stops guessing. It recomputed every
            # team total with the spreadsheet arithmetic regardless — fine while
            # that was the live formula, wrong the day the config moved to
            # 'computed', and wrong silently, because both produce a number.
            "formula": config.get("grade_formula", "sheet"),
            "quality_scale": config.get("quality_scale", 1.0),
            # The rule itself, so the page can explain a team's points rather
            # than just print them.
            "quality_rule": {
                "wq_top5": config.get("wq_top5", 0.0),
                "wq_top10": config.get("wq_top10", 0.0),
                "wq_top25": config.get("wq_top25", 0.0),
                "wq_other": config.get("wq_other", 0.0),
                "lq_ranked": config.get("lq_ranked", 0.0),
                "lq_unranked_fbs": config.get("lq_unranked_fbs", 0.0),
                "lq_fcs": config.get("lq_fcs", 0.0),
            },
            "loss_sign": config.get("sheet_loss_sign", -1.0),
            "raw_wl": config.get("sheet_raw_wl", 0.0),
        },
        # ── V2: the four scoreboards, and the challenger table ───────────────
        #
        # Separate keys, never one "record". A reader who wants a single number
        # has to choose which question they are asking, which is the point.
        "v2": build_v2_block(conn, args.sport, season),
        "dispersion": disp,
        "preseason": preseason,
        "alerts": load_alerts(),
        "excluded_blowouts": n_all - len(bets),
        "excluded_by_reason": excluded,
        "blowout_line": best_bets.BLOWOUT_LINE,
        "record": {k: v for k, v in rec.items() if k not in ("rows", "curve")},
        "curve": rec.get("curve", []),
        "bets": bets,
        "schedule": slate,
        "week0": bool(labels),
        "grades_sync": load_json("output/grades_sync.json"),
        "health": health(conn, args.sport, season),
        # The model's own record, cut every way that could change the answer, and
        # the bets Grant actually placed. Two different records on purpose — see
        # the header of bet_log.py for why conflating them is the classic mistake.
        # `mybets` now carries only the rows still coming from the Google Sheet; the
        # bets he types on the site live in his browser and never reach this file.
        "tracking": tracking.summary(conn, args.sport, season=None, week_labels=labels),
        "mybets": mybets,
        "teams": team_snapshot(conn, args.sport, grade_season, model=rater_model),
        "efficiency": efficiency_trend(conn, args.sport, eff_season),
        "results": team_results(conn, args.sport, eff_season),
        "movement": line_movement(conn, args.sport, season),
    }

    # THE LAST GATE, ON THE EXACT OBJECT THAT GETS WRITTEN. Not on the pieces, not
    # on an earlier copy: on the bundle as assembled, so a field added anywhere
    # between here and the top of this function is still checked. It refuses to
    # write rather than writing and warning, because a bundle that has been served
    # once has been fetched.
    problems = public_export.audit(bundle)
    if problems:
        print("REFUSING TO WRITE the research bundle — it carries private data:")
        for p in problems[:12]:
            print("  %s" % p)
        raise SystemExit(
            "This bundle is served in the clear. Remove the fields above, or add "
            "them to public_export.FORBIDDEN_KEYS if they are genuinely safe.")

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
