"""
ledger.py — lock picks before kickoff, grade them after, never revise them.

The published track record is only worth something if it cannot be edited after
the fact. Two functions enforce that:

    lock()   writes a pick for any game that has NOT started yet.
             Uses INSERT OR IGNORE, so re-running never overwrites an existing
             pick. The first number recorded is the permanent one.

    grade()  fills in the result columns once a game is final, and refuses to
             touch model_margin, market_margin_at_pick or ats_pick.

Anything that has already kicked off is skipped by lock() entirely. That is the
rule that makes the record honest: a pick that appears after the game started
is not a prediction.
"""

import datetime as dt

import db
import grading
import metrics


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _parse_kickoff(s):
    if not s:
        return None
    txt = str(s).replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(txt)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M"):
            try:
                d = dt.datetime.strptime(txt[:len(fmt) + 2], fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


# How far ahead a pick may be locked.
#
# Three days, not a week, because information only improves as kickoff approaches:
# the grades are fresher, the line is closer to closing (which is what makes the CLV
# column mean anything), and injuries are known. Run daily, this locks every game
# one to three days out — a Thursday night game on Monday or Tuesday, a Saturday
# game on Wednesday or Thursday.
#
# Three rather than two so that ONE missed run cannot let a game kick off unlocked.
# `missed_locks` below reports it if that ever happens anyway, because a pick that
# was never recorded makes the record quietly better than the model was.
LOCK_WINDOW_DAYS = 3


def lock(conn, sport, picks, config_label, now=None, within_days=LOCK_WINDOW_DAYS):
    """
    Record picks for the UPCOMING slate. Returns (locked, skipped_started, deferred).

    `picks` are rows from predict.generate().

    THE WINDOW IS THE POINT, and it was missing. This locked every game that had not
    yet kicked off -- which on the first run of the season meant all 788 of them,
    weeks 1 through 15, stamped 6 August. `INSERT OR IGNORE` then made those first
    picks permanent, so:

      * A week-12 pick was made from August ratings in which the quality-points half
        of the formula is literally zero, and could never be revised.
      * Every week of film Grant grades after August was unable to reach the record,
        which is the entire edge this model claims to have.
      * `market_margin_at_pick` was an August line for a game nobody had priced yet,
        making the closing-line-value column meaningless.

    Locking early does not protect a record from hindsight; it only guarantees the
    picks are worse. What protects the record is locking BEFORE KICKOFF, which a
    weekly window does just as absolutely.
    """
    now = now or _now()
    horizon = now + dt.timedelta(days=within_days) if within_days else None
    rows, started, deferred = [], 0, 0
    for p in picks:
        ko = _parse_kickoff(p.get("kickoff"))
        if ko is not None and ko <= now:
            started += 1
            continue
        if horizon is not None and ko is not None and ko > horizon:
            deferred += 1                 # a later week; it gets its own lock, later
            continue
        rows.append({
            "game_id": p.get("game_id"),
            "sport": sport,
            "season": p.get("season"),
            "week": p.get("week"),
            "home_team": p.get("home"),
            "away_team": p.get("away"),
            "kickoff": p.get("kickoff"),
            "published_at": now.isoformat(),
            "config_label": config_label,
            "model_margin": p.get("model_margin"),
            "market_margin_at_pick": p.get("market_margin"),
            "model_total": p.get("model_total"),
            "market_total_at_pick": p.get("market_total"),
            "ats_pick": p.get("ats_pick"),
            "ml_pick": p.get("ml_pick"),
            "ou_pick": p.get("ou_pick"),
            # The price is part of the pick. Without it a moneyline record cannot
            # be scored at all -- a 50% hit rate is excellent at +150 and ruinous
            # at -150 -- so it is locked at the same moment and never revised.
            "ml_odds_at_pick": p.get("ml_odds"),
        })
    if rows:
        # OR IGNORE, not ON CONFLICT UPDATE: an existing pick is final.
        conn.executemany(
            """INSERT OR IGNORE INTO picks_log
               (game_id, sport, season, week, home_team, away_team, kickoff,
                published_at, config_label, model_margin, market_margin_at_pick,
                model_total, market_total_at_pick, ats_pick, ml_pick, ou_pick,
                ml_odds_at_pick)
               VALUES (:game_id, :sport, :season, :week, :home_team, :away_team,
                       :kickoff, :published_at, :config_label, :model_margin,
                       :market_margin_at_pick, :model_total, :market_total_at_pick,
                       :ats_pick, :ml_pick, :ou_pick, :ml_odds_at_pick)""", rows)
        conn.commit()
    inserted = conn.execute(
        "SELECT COUNT(*) c FROM picks_log WHERE sport=? AND published_at=?",
        (sport, now.isoformat())).fetchone()["c"]
    return inserted, started, deferred


def missed_locks(conn, sport, season=None, now=None):
    """
    Games that kicked off with no pick recorded. Returns (count, examples).

    A pick that was never locked cannot be graded, so it silently leaves the record
    — and it leaves it in the most flattering possible way, because the games that
    go unlocked are the ones a broken run skipped, not a random sample. Counting
    them is the only thing standing between "the model went 5-3" and "the model went
    5-3 on the games the pipeline happened to be awake for".

    Only games the model could actually price are counted, and that takes TWO
    conditions, not one. A fixture with no line is one it declines to bet. A
    fixture where neither team is rated is one it has no opinion on at all.

    THE SECOND CONDITION WAS MISSING AND THE METRIC CRIED WOLF. It read 45 on
    2026-09-02 while exactly 8 games had kicked off, all locked and graded.
    tools/diagnose_locks.py classified all 45 against the production database:
    every one was an FCS-vs-FCS fixture -- Maine at Towson, Mercyhurst at
    Youngstown State -- with a line but no rated team on either side. Genuinely
    missed: zero. CFBD's schedule carries all divisions, and 750 of the season's
    1,638 games have no FBS team in them; research_export.schedule already drops
    exactly these, so the two now agree on what "a game this model plays" means.

    A number that reads 45 every night when nothing is wrong is worse than no
    number, because the night it means something nobody will look.

    THE GUARD MATTERS AS MUCH AS THE FILTER: if a season has no grades loaded at
    all, every game is "unrated" and this would go permanently, silently quiet --
    which is the failure it exists to catch. So the filter only applies when
    there are grades to filter by.
    """
    now = now or _now()
    have_grades = conn.execute(
        "SELECT 1 FROM grades WHERE sport=? AND (? IS NULL OR season=?) LIMIT 1",
        (sport, season, season)).fetchone() is not None
    rated_only = """
              AND EXISTS (SELECT 1 FROM grades gr
                           WHERE gr.sport = g.sport AND gr.season = g.season
                             AND gr.team IN (g.home_team, g.away_team))""" \
        if have_grades else ""
    rows = conn.execute(
        """SELECT g.game_id, g.season, g.week, g.kickoff, g.home_team, g.away_team
             FROM games g
             JOIN lines l ON l.game_id = g.game_id
        LEFT JOIN picks_log p ON p.game_id = g.game_id
            WHERE g.sport = ? AND p.game_id IS NULL
              AND l.home_margin IS NOT NULL
              AND g.kickoff IS NOT NULL AND g.kickoff <= ?
              AND (? IS NULL OR g.season = ?)""" + rated_only + """
         ORDER BY g.kickoff DESC""",
        (sport, now.isoformat(), season, season)).fetchall()
    return len(rows), [dict(r) for r in rows[:5]]


def stale_locks(conn, sport, season=None, now=None, slack_days=2):
    """
    Picks sitting in the ledger that were locked far outside the window.

    Returns (count, examples). Zero when the pipeline is healthy.

    `missed_locks` above catches a game with NO pick. This catches the opposite and
    more dangerous shape: a pick that IS there, so nothing looks missing, but that
    was made weeks before kickoff and can never be replaced -- `lock` writes with
    INSERT OR IGNORE, so an existing row silently wins over every better pick the
    model would make later.

    THAT IS NOT HYPOTHETICAL, IT IS WHAT HAPPENED. The 6 August run locked all 888
    games of the 2026 season in one go. Adding LOCK_WINDOW_DAYS afterwards fixed
    nothing, because every future game already held an August row: the window was
    correct, in the code, and INERT. Saturday of week 1 was still being played from
    an August pick a month later, and the only visible symptom was a bad record.

    A rule that cannot take effect looks exactly like a rule that is working.

    `slack_days` above the window, so a pick locked a day early by a scheduler that
    ran late is not reported. Anything beyond that had a better pick available and
    was not allowed to make it.
    """
    now = now or _now()
    horizon_days = LOCK_WINDOW_DAYS + slack_days
    rows = conn.execute(
        """SELECT p.game_id, p.season, p.week, p.kickoff, p.published_at,
                  p.home_team, p.away_team
             FROM picks_log p
            WHERE p.sport = ? AND p.graded_at IS NULL AND p.voided_at IS NULL
              AND p.kickoff IS NOT NULL AND p.kickoff > ?
              AND (? IS NULL OR p.season = ?)
              AND julianday(p.kickoff) - julianday(p.published_at) > ?
         ORDER BY p.kickoff""",
        (sport, now.isoformat(), season, season, horizon_days)).fetchall()
    return len(rows), [dict(r) for r in rows[:5]]


def _legacy_result(model_value, line, actual):
    """The OLD grader's answer, reproduced exactly. Do not use for new work.

    Side inferred from `model_value - line`, graded at the close. Kept only so the
    legacy columns keep their historical meaning during the transition; see the
    note in `grade` for why that meaning was wrong.
    """
    if line is None or model_value is None:
        return None
    edge = model_value - line
    diff = actual - line
    if edge == 0:
        return None
    if diff == 0:
        return "P"
    return "W" if (edge > 0) == (diff > 0) else "L"


def grade(conn, sport, now=None):
    """Grade every locked pick whose game is now final. Returns how many were graded.

    TWICE, AND FROM THE SIDE THAT WAS PUBLISHED. This function used to compute

        edge = model_margin - closing_margin

    and take the sign of that as the side. `ats_pick` -- the side actually
    published -- was never read, so when the market moved across the model's
    number the graded side flipped and the row recorded a wager nobody made. It
    also graded only at the close, while the site called the result the record of
    the published pick.

    Both are now recorded, separately and forever:

        ats_result_at_pick   the side published, at the line it was locked at.
                             This is the official result.
        ats_result_at_close  the same side, at the closing line. A diagnostic.

    The legacy `ats_result` / `ou_result` columns are still written with their
    original close-based meaning so that no existing reader changes answer
    underneath itself during the transition. They are not the record any more;
    `tracking` reads the _at_pick columns.
    """
    now = now or _now()
    rows = conn.execute(
        """SELECT p.game_id, p.model_margin, p.market_margin_at_pick,
                  p.model_total, p.market_total_at_pick,
                  p.ats_pick, p.ou_pick, p.ml_pick, p.home_team, p.away_team,
                  g.home_score, g.away_score,
                  l.home_margin AS closing_margin, l.total AS closing_total,
                  l.home_ml, l.away_ml
           FROM picks_log p
           JOIN games g ON g.game_id = p.game_id
           LEFT JOIN lines l ON l.game_id = p.game_id
           WHERE p.sport = ? AND p.graded_at IS NULL
             AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL""",
        (sport,)).fetchall()

    updates = []
    for r in rows:
        actual_margin = r["home_score"] - r["away_score"]
        actual_total = r["home_score"] + r["away_score"]
        locked_m = r["market_margin_at_pick"]
        locked_t = r["market_total_at_pick"]
        # No close recorded means the last number we saw IS the last number there
        # was. Falling back to the locked line makes the two columns equal, which
        # is honest; inventing a close would not be.
        close_m = r["closing_margin"] if r["closing_margin"] is not None else locked_m
        close_t = r["closing_total"] if r["closing_total"] is not None else locked_t

        teams = {"home_team": r["home_team"], "away_team": r["away_team"]}
        try:
            ats_pick_res = grading.grade_spread_pick(
                side=r["ats_pick"], home_margin_line=locked_m,
                actual_home_margin=actual_margin, **teams)
            ats_close_res = grading.grade_spread_pick(
                side=r["ats_pick"], home_margin_line=close_m,
                actual_home_margin=actual_margin, **teams)
        except ValueError as e:
            # An ats_pick naming neither team is an upstream defect (alias drift,
            # a mis-joined row). Grading it as a loss would book a defeat for a
            # wager that never existed, so it is left ungraded and said out loud.
            print("  WARNING: %s — pick left ungraded" % e)
            ats_pick_res = ats_close_res = None

        ou_pick_res = grading.grade_total_pick(
            side=r["ou_pick"], total_line=locked_t, actual_total=actual_total)
        ou_close_res = grading.grade_total_pick(
            side=r["ou_pick"], total_line=close_t, actual_total=actual_total)

        try:
            ml = grading.grade_moneyline_pick(
                team=r["ml_pick"], actual_home_margin=actual_margin, **teams)
        except ValueError as e:
            print("  WARNING: %s — moneyline left ungraded" % e)
            ml = None
        close_ml = (r["home_ml"] if r["ml_pick"] == r["home_team"]
                    else r["away_ml"] if r["ml_pick"] == r["away_team"] else None)

        # LEGACY COLUMNS, LEGACY MEANING. Reproduced exactly as the old grader
        # computed them -- close-based, side inferred from the model number --
        # so that anything still reading them keeps getting the same answer it
        # got yesterday. They are preserved, not trusted.
        legacy_ats = _legacy_result(r["model_margin"], close_m, actual_margin)
        legacy_ou = _legacy_result(r["model_total"], close_t, actual_total)

        updates.append({
            "game_id": r["game_id"], "closing_margin": close_m, "closing_total": close_t,
            "actual_margin": actual_margin, "actual_total": actual_total,
            "ats_result": legacy_ats, "ou_result": legacy_ou,
            "ats_result_at_pick": ats_pick_res, "ats_result_at_close": ats_close_res,
            "ou_result_at_pick": ou_pick_res, "ou_result_at_close": ou_close_res,
            "ml_result": ml, "closing_ml": close_ml,
            "grading_version": db.GRADING_VERSION,
            "graded_at": now.isoformat(),
        })

    if updates:
        # Only result columns are written. The pick itself is untouched.
        conn.executemany(
            """UPDATE picks_log SET
                 closing_margin=:closing_margin, closing_total=:closing_total,
                 actual_margin=:actual_margin, actual_total=:actual_total,
                 ats_result=:ats_result, ou_result=:ou_result,
                 ats_result_at_pick=:ats_result_at_pick,
                 ats_result_at_close=:ats_result_at_close,
                 ou_result_at_pick=:ou_result_at_pick,
                 ou_result_at_close=:ou_result_at_close,
                 ml_result=:ml_result, closing_ml=:closing_ml,
                 grading_version=:grading_version,
                 graded_at=:graded_at
               WHERE game_id=:game_id""", updates)
        conn.commit()
    return len(updates)


def record(conn, sport, season=None):
    """Summarize the graded ledger — the numbers the public page shows."""
    q = ("SELECT * FROM picks_log WHERE sport=? AND graded_at IS NOT NULL"
         " AND " + db.NOT_VOIDED)
    args = [sport]
    if season:
        q += " AND season=?"
        args.append(season)
    rows = [dict(r) for r in conn.execute(q + " ORDER BY kickoff", args)]

    w = sum(1 for r in rows if r["ats_result"] == "W")
    l = sum(1 for r in rows if r["ats_result"] == "L")
    p = sum(1 for r in rows if r["ats_result"] == "P")
    ow = sum(1 for r in rows if r["ou_result"] == "W")
    ol = sum(1 for r in rows if r["ou_result"] == "L")

    out = {"n": len(rows), "ats_w": w, "ats_l": l, "ats_push": p,
           "ou_w": ow, "ou_l": ol, "rows": rows}
    if w + l:
        out["ats_pct"] = 100.0 * w / (w + l)
        lo, hi = metrics.wilson_ci(w, w + l)
        out["ats_ci95"] = (100 * lo, 100 * hi)
        out["roi"] = 100.0 * (w * metrics.WIN_UNITS - l) / (w + l)
        out["proven"] = 100 * lo > 100 * metrics.BREAK_EVEN
    if ow + ol:
        out["ou_pct"] = 100.0 * ow / (ow + ol)
        out["ou_roi"] = 100.0 * (ow * metrics.WIN_UNITS - ol) / (ow + ol)

    # Cumulative units, in kickoff order — the shape of the public chart.
    curve, units = [], 0.0
    for r in rows:
        if r["ats_result"] == "W":
            units += metrics.WIN_UNITS
        elif r["ats_result"] == "L":
            units -= 1.0
        curve.append({"kickoff": r["kickoff"], "units": round(units, 3)})
    out["curve"] = curve
    out["units"] = round(units, 2)
    return out
