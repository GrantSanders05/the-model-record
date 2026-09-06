"""
test_v2_integrity.py — the V2 invariant suite.

One file for the rules that, if they break, make the record mean something other
than what it says. Every section ends by breaking the thing it just proved, so a
test that cannot fail is caught here rather than believed for a week.

    python3 tools/test_v2_integrity.py

Sections follow the V2 build document:
  25.1 grading        25.2 provenance      25.3 as-of leakage
  25.4 market         25.5 horizons        25.6 strategy
  25.7 migration      25.8 state journal   25.9 privacy
  25.10 research models
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import grading            # noqa: E402

P = F = 0


def ok(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print("  [PASS] %s" % name)
    else:
        F += 1
        print("  [FAIL] %s%s" % (name, (" — " + str(detail)) if detail else ""))


def near(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) < tol


# ══ 25.1 GRADING ══════════════════════════════════════════════════════════════
#
# House convention, everywhere: a home margin of +7 means the HOME team is
# favoured by 7. It is the convention the whole repository already uses and the
# one thing here that must never be renegotiated.

print("── the locked line and the close are different questions ──")

# THE CASE THIS SECTION EXISTS FOR (build doc §2.7 Test A).
# Picked A when A was laying 2.5. The line moved to 3.5. A won by 3.
# The wager placed WON. The same side at the closing number LOST. Both are true
# and the record has to be able to say both.
A_PICK = dict(side="A", home_team="A", away_team="B", actual_home_margin=3)
ok("a pick that wins at the number it was locked at",
   grading.grade_spread_pick(home_margin_line=2.5, **A_PICK) == "W")
ok("...and loses at the number it closed at",
   grading.grade_spread_pick(home_margin_line=3.5, **A_PICK) == "L")

# §2.7 Test B — THE ORIGINAL SIDE IS IMMUTABLE.
# Model said home by 7, market was home by 6, so HOME was picked. The market
# then moved to 8. Recomputing the side from `model_margin - closing_margin`
# gives 7 - 8 = -1 and flips the pick to AWAY, which grades a bet nobody made.
# Home won by 9: the side actually taken covered.
ok("the side taken is graded, not one recomputed from the close",
   grading.grade_spread_pick(side="HOME", home_team="HOME", away_team="AWAY",
                             home_margin_line=8.0, actual_home_margin=9) == "W",
   "a recomputed side would call this a loss")

print("\n── every branch of a spread ──")
S = dict(home_team="H", away_team="A")
ok("home side, home covers", grading.grade_spread_pick(side="H", home_margin_line=3.0, actual_home_margin=7, **S) == "W")
ok("home side, home fails to cover", grading.grade_spread_pick(side="H", home_margin_line=3.0, actual_home_margin=1, **S) == "L")
ok("away side, away covers", grading.grade_spread_pick(side="A", home_margin_line=3.0, actual_home_margin=1, **S) == "W")
ok("away side, away fails to cover", grading.grade_spread_pick(side="A", home_margin_line=3.0, actual_home_margin=7, **S) == "L")
ok("exactly on the number is a push", grading.grade_spread_pick(side="H", home_margin_line=3.0, actual_home_margin=3, **S) == "P")
ok("...a push for the other side too", grading.grade_spread_pick(side="A", home_margin_line=3.0, actual_home_margin=3, **S) == "P")
# An away favourite: -6 means the AWAY team is laying 6.
ok("away favourite, laying and covering",
   grading.grade_spread_pick(side="A", home_margin_line=-6.0, actual_home_margin=-10, **S) == "W")
ok("away favourite, laying and not covering",
   grading.grade_spread_pick(side="A", home_margin_line=-6.0, actual_home_margin=-2, **S) == "L")
ok("a missing line grades nothing",
   grading.grade_spread_pick(side="H", home_margin_line=None, actual_home_margin=7, **S) is None)
ok("a missing result grades nothing",
   grading.grade_spread_pick(side="H", home_margin_line=3.0, actual_home_margin=None, **S) is None)
ok("a missing side grades nothing",
   grading.grade_spread_pick(side=None, home_margin_line=3.0, actual_home_margin=7, **S) is None)

# A side naming a team that is not in the game is a bug upstream, not a loss.
# Returning "L" would quietly book a defeat for a wager that never existed.
bad = None
try:
    grading.grade_spread_pick(side="Nebraska", home_margin_line=3.0, actual_home_margin=7, **S)
except ValueError as e:
    bad = str(e)
ok("a side that is neither team raises, rather than grading as a loss", bad is not None, bad)

print("\n── totals ──")
ok("over wins above the number", grading.grade_total_pick(side="OVER", total_line=52.5, actual_total=60) == "W")
ok("over loses below it", grading.grade_total_pick(side="OVER", total_line=52.5, actual_total=40) == "L")
ok("under wins below the number", grading.grade_total_pick(side="UNDER", total_line=52.5, actual_total=40) == "W")
ok("under loses above it", grading.grade_total_pick(side="UNDER", total_line=52.5, actual_total=60) == "L")
ok("exactly on the total is a push", grading.grade_total_pick(side="OVER", total_line=52.0, actual_total=52) == "P")
ok("...for the under as well", grading.grade_total_pick(side="UNDER", total_line=52.0, actual_total=52) == "P")
ok("a missing total grades nothing", grading.grade_total_pick(side="OVER", total_line=None, actual_total=52) is None)
tbad = None
try:
    grading.grade_total_pick(side="SIDEWAYS", total_line=52.0, actual_total=60)
except ValueError as e:
    tbad = str(e)
ok("a nonsense total side raises", tbad is not None, tbad)

print("\n── moneyline ──")
M = dict(home_team="H", away_team="A")
ok("home wins outright", grading.grade_moneyline_pick(team="H", actual_home_margin=7, **M) == "W")
ok("home loses outright", grading.grade_moneyline_pick(team="H", actual_home_margin=-7, **M) == "L")
ok("away wins outright", grading.grade_moneyline_pick(team="A", actual_home_margin=-7, **M) == "W")
# College football cannot tie in regulation, but grading a 0 as a loss would be a
# silent wrong answer rather than a rare one.
ok("a tie is a push, not a loss", grading.grade_moneyline_pick(team="H", actual_home_margin=0, **M) == "P")

print("\n── what a win is worth, and when we do not know ──")
ok("-110 pays 0.909", near(grading.american_profit_units("W", -110), 100 / 110))
ok("-150 pays 0.667", near(grading.american_profit_units("W", -150), 100 / 150))
ok("+150 pays 1.5", near(grading.american_profit_units("W", 150), 1.5))
ok("a loss costs the unit", near(grading.american_profit_units("L", -110), -1.0))
ok("a push costs nothing", near(grading.american_profit_units("P", -110), 0.0))

# §2.7 Test C. The whole point: a winning bet at an unrecorded price has an
# UNKNOWN return, not a 0.909 one. Filling in -110 is how a synthetic number
# becomes a published ROI.
ok("a win at an unknown price returns None, not 0.909",
   grading.american_profit_units("W", None) is None)
ok("an odds value of zero is not a price", grading.american_profit_units("W", 0) is None)

print("\n── closing line value ──")
# Positive CLV means the number we took was better than the number it closed at.
ok("home side, line moved our way",
   near(grading.line_clv(side="home", locked=2.5, closing=3.5), 1.0))
ok("home side, line moved against us",
   near(grading.line_clv(side="home", locked=3.5, closing=2.5), -1.0))
ok("away side, line moved our way",
   near(grading.line_clv(side="away", locked=3.5, closing=2.5), 1.0))
ok("away side, line moved against us",
   near(grading.line_clv(side="away", locked=2.5, closing=3.5), -1.0))
# An OVER is cheaper the LOWER the number taken, so it gains when the total
# closes higher. An UNDER is the mirror. Getting this backwards is easy, which
# is why both directions are asserted for both sides.
ok("over gains when the total closes higher",
   near(grading.total_clv(side="OVER", locked=52.5, closing=54.5), 2.0))
ok("over loses when the total closes lower",
   near(grading.total_clv(side="OVER", locked=54.5, closing=52.5), -2.0))
ok("under gains when the total closes lower",
   near(grading.total_clv(side="UNDER", locked=54.5, closing=52.5), 2.0))
ok("under loses when the total closes higher",
   near(grading.total_clv(side="UNDER", locked=52.5, closing=54.5), -2.0))
ok("no close, no CLV", grading.line_clv(side="home", locked=2.5, closing=None) is None)


# ══ THE LEDGER ACTUALLY USES THEM ═════════════════════════════════════════════
#
# The pure functions above can be perfect while the ledger keeps its own copy of
# the arithmetic -- which is exactly how the old grader survived so long. These
# build a real database, run the real `ledger.grade`, and read the columns back.

print("\n── ledger.grade writes both results, from the published side ──")

import sqlite3                                                  # noqa: E402
import tempfile                                                 # noqa: E402
import db as _db                                                # noqa: E402
import ledger                                                   # noqa: E402


def fixture_db(picks, games, lines=()):
    """A real on-disk database with the real schema. Returns an open connection."""
    path = os.path.join(tempfile.mkdtemp(), "fixture.db")
    conn = _db.connect(path)
    for g in games:
        conn.execute(
            "INSERT INTO games (game_id, sport, season, week, home_team, away_team,"
            " kickoff, home_score, away_score, neutral_site, home_div, away_div)"
            " VALUES (:game_id,'cfb',2026,1,:home_team,:away_team,:kickoff,"
            " :home_score,:away_score,0,'fbs','fbs')", g)
    for ln in lines:
        conn.execute(
            "INSERT INTO lines (game_id, provider, home_margin, total, home_ml, away_ml)"
            " VALUES (:game_id,'Test',:home_margin,:total,:home_ml,:away_ml)", ln)
    for p in picks:
        conn.execute(
            "INSERT INTO picks_log (game_id, sport, season, week, home_team, away_team,"
            " kickoff, published_at, config_label, model_margin, market_margin_at_pick,"
            " model_total, market_total_at_pick, ats_pick, ou_pick, ml_pick)"
            " VALUES (:game_id,'cfb',2026,1,:home_team,:away_team,:kickoff,"
            " '2026-09-01T00:00:00+00:00','test',:model_margin,:market_margin_at_pick,"
            " :model_total,:market_total_at_pick,:ats_pick,:ou_pick,:ml_pick)", p)
    conn.commit()
    return conn


# The build document's Test A and Test B, run through the real grader.
# HOME was picked at a line of +6 (model said +7). The market closed at +8.
# Home won by 9. The published side covered BOTH numbers; a side recomputed
# against the close would have been AWAY and would read this as a loss.
_conn = fixture_db(
    games=[dict(game_id="g1", home_team="H", away_team="A",
                kickoff="2026-09-05T18:00:00.000Z", home_score=29, away_score=20)],
    lines=[dict(game_id="g1", home_margin=8.0, total=55.0, home_ml=-250, away_ml=210)],
    picks=[dict(game_id="g1", home_team="H", away_team="A",
                kickoff="2026-09-05T18:00:00.000Z", model_margin=7.0,
                market_margin_at_pick=6.0, model_total=52.0,
                market_total_at_pick=50.0, ats_pick="H", ou_pick="OVER", ml_pick="H")])
n = ledger.grade(_conn, "cfb")
ok("the grader reports how many rows it graded", n == 1, n)
row = dict(_conn.execute("SELECT * FROM picks_log WHERE game_id='g1'").fetchone())
ok("graded at the locked line", row["ats_result_at_pick"] == "W", row["ats_result_at_pick"])
ok("...and separately at the close", row["ats_result_at_close"] == "W", row["ats_result_at_close"])
ok("the legacy column keeps its old close-based answer",
   row["ats_result"] == "L", row["ats_result"])
ok("...which is exactly the side-flip the new columns exist to replace",
   row["ats_result"] != row["ats_result_at_close"])
ok("the row records which rules graded it",
   row["grading_version"] == _db.GRADING_VERSION, row["grading_version"])
ok("the total is graded from the published direction",
   row["ou_result_at_pick"] == "L" and row["ou_result_at_close"] == "L",
   (row["ou_result_at_pick"], row["ou_result_at_close"]))
ok("the moneyline is graded", row["ml_result"] == "W", row["ml_result"])

# A pick that wins at the locked number and loses at the close -- the case the
# public record was silently reporting the wrong side of.
_conn2 = fixture_db(
    games=[dict(game_id="g2", home_team="H", away_team="A",
                kickoff="2026-09-05T18:00:00.000Z", home_score=24, away_score=21)],
    lines=[dict(game_id="g2", home_margin=3.5, total=45.0, home_ml=-160, away_ml=140)],
    picks=[dict(game_id="g2", home_team="H", away_team="A",
                kickoff="2026-09-05T18:00:00.000Z", model_margin=6.0,
                market_margin_at_pick=2.5, model_total=50.0,
                market_total_at_pick=48.0, ats_pick="H", ou_pick="OVER", ml_pick="H")])
ledger.grade(_conn2, "cfb")
row2 = dict(_conn2.execute("SELECT * FROM picks_log WHERE game_id='g2'").fetchone())
ok("won at the number locked", row2["ats_result_at_pick"] == "W", row2["ats_result_at_pick"])
ok("lost at the number it closed at", row2["ats_result_at_close"] == "L", row2["ats_result_at_close"])
# Total: took OVER 48, actual 45 -> lost at the locked number. It closed at 45,
# exactly the score, so the same side pushed at the close. Two different answers
# from one side, which is the whole reason both columns exist.
ok("the total lost at the number locked", row2["ou_result_at_pick"] == "L", row2["ou_result_at_pick"])
ok("...and pushed at the number it closed at", row2["ou_result_at_close"] == "P", row2["ou_result_at_close"])

# A pick whose side names neither team must not be graded as a loss.
_conn3 = fixture_db(
    games=[dict(game_id="g3", home_team="H", away_team="A",
                kickoff="2026-09-05T18:00:00.000Z", home_score=24, away_score=21)],
    lines=[dict(game_id="g3", home_margin=3.5, total=48.0, home_ml=None, away_ml=None)],
    picks=[dict(game_id="g3", home_team="H", away_team="A",
                kickoff="2026-09-05T18:00:00.000Z", model_margin=6.0,
                market_margin_at_pick=2.5, model_total=50.0,
                market_total_at_pick=48.0, ats_pick="Nebraska", ou_pick=None, ml_pick=None)])
ledger.grade(_conn3, "cfb")
row3 = dict(_conn3.execute("SELECT * FROM picks_log WHERE game_id='g3'").fetchone())
ok("a side naming neither team is left ungraded, not booked as a loss",
   row3["ats_result_at_pick"] is None, row3["ats_result_at_pick"])


# ── proving this section can fail ────────────────────────────────────────────
print("\n── proving these can fail ──")
_before = F
_bad = grading.grade_spread_pick(side="A", home_team="A", away_team="B",
                                 home_margin_line=2.5, actual_home_margin=3)
ok("[control] the locked-line case must NOT grade as a loss", _bad == "L", "got %r" % _bad)
if F > _before:
    print("  ...it failed, as required. The grading assertions are real.")
    F = _before
else:
    F += 1
    print("  [FAIL] the control passed — these assertions prove nothing")

print("\n" + "=" * 62)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
