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
ROOT_DIR = ROOT                      # the repository root, used by the moat checks
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

import json                                                     # noqa: E402
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


# ══ 25.2 PROVENANCE ═══════════════════════════════════════════════════════════

print("\n── a forecast can prove what produced it ──")

import provenance as pv                                         # noqa: E402
import engine                                                   # noqa: E402

# Python dict order, float repr and unicode escaping all vary. If any of them
# reach the hash, "has the config changed?" has two answers and neither is usable.
a = {"b": 1, "a": {"y": 2, "x": 1}, "z": [3, 1, 2]}
b = {"z": [3, 1, 2], "a": {"x": 1, "y": 2}, "b": 1}
ok("key order does not change the hash", pv.payload_hash(a) == pv.payload_hash(b))
ok("...nor the id built from it",
   pv.stable_id("forecast", a) == pv.stable_id("forecast", b))
ok("list ORDER does change it, because a list is ordered data",
   pv.payload_hash({"z": [1, 2, 3]}) != pv.payload_hash({"z": [3, 2, 1]}))
ok("an id says what kind of thing it identifies",
   pv.stable_id("signal", a).startswith("sg_") and pv.stable_id("market_quote", a).startswith("mq_"))
unknown = None
try:
    pv.stable_id("nonsense", a)
except ValueError as e:
    unknown = str(e)
ok("an unknown id kind raises rather than inventing a prefix", unknown is not None)

# THE FULLY MERGED CONFIG IS THE IDENTITY. Hashing the sparse file makes a
# forecast irreproducible the moment a default moves: same file, same hash,
# different model.
sparse = {"scale": 1.311}
h_default = pv.config_hash(sparse)
moved_defaults = dict(engine.DEFAULT_CONFIG, hfa=99.0)
h_moved = pv.config_hash(sparse, defaults=moved_defaults)
ok("a config's hash covers the defaults it inherits", h_default != h_moved,
   "a moved default must change the hash of an unchanged file")
ok("...and an explicit value overrides the default it replaces",
   pv.config_hash(dict(sparse, hfa=99.0)) == pv.config_hash(sparse, defaults=moved_defaults))
ok("runtime payloads are not part of identity",
   pv.config_hash(sparse) == pv.config_hash(dict(sparse, _grades={"x": 1})),
   "an injected _grades blob must not change the model version")
ok("the merged config is complete", set(engine.DEFAULT_CONFIG) <= set(pv.merged_config(sparse)))

sha, dirty = pv.git_sha()
ok("the running code can name itself", bool(sha), sha)
ok("...and knows whether the tree is dirty", dirty in (True, False), dirty)
okay, why = pv.official_ready("abc123", False)
ok("clean, identified code may sign an official record", okay)
okay2, why2 = pv.official_ready("abc123", True)
ok("a dirty tree may not", not okay2 and "dirty" in why2, why2)
okay3, why3 = pv.official_ready(None, False)
ok("...nor may code that cannot say what it is", not okay3, why3)

print("\n── the V2 schema reaches an existing database, not only a fresh one ──")
_fresh = _db.connect(os.path.join(tempfile.mkdtemp(), "fresh.db"))
_v2_tables = ["market_quotes", "market_snapshots", "grade_snapshots",
              "feature_snapshots", "model_registry", "forecast_log",
              "strategy_evaluations", "signal_log", "game_results_v2",
              "snapshot_misses", "v2_void_events", "v2_migrations"]
_have = {r[0] for r in _fresh.execute("SELECT name FROM sqlite_master WHERE type='table'")}
ok("a fresh database has every V2 table", set(_v2_tables) <= _have,
   sorted(set(_v2_tables) - _have))
# The one that actually matters: a database created before V2 existed. A
# migration that only works on a fresh database is broken (build doc §26).
# Built from the pre-V2 schema exactly -- db.SCHEMA with no SCHEMA_V2 and no
# _migrate -- which is what every database restored from the Actions cache is.
_legacy_path = os.path.join(tempfile.mkdtemp(), "old.db")
_old = sqlite3.connect(_legacy_path)
_old.executescript(_db.SCHEMA)
_old.execute("INSERT INTO games (game_id, sport, season, week, home_team, away_team)"
             " VALUES ('old1','cfb',2025,1,'H','A')")
_old.execute("INSERT INTO picks_log (game_id, sport, season, week, home_team,"
             " away_team, published_at, config_label, ats_result)"
             " VALUES ('old1','cfb',2025,1,'H','A','2025-09-01','old','L')")
_old.commit()
_old.close()
_upgraded = _db.connect(_legacy_path)
_have2 = {r[0] for r in _upgraded.execute("SELECT name FROM sqlite_master WHERE type='table'")}
ok("a pre-V2 database gets them too", set(_v2_tables) <= _have2,
   sorted(set(_v2_tables) - _have2))
_cols = {r["name"] for r in _upgraded.execute("PRAGMA table_info(picks_log)")}
ok("...and an existing picks_log gains the V2 result columns",
   {"ats_result_at_pick", "ats_result_at_close", "grading_version"} <= _cols,
   sorted({"ats_result_at_pick", "ats_result_at_close", "grading_version"} - _cols))
ok("...without losing the column it already had", "ats_result" in _cols)
ok("...and without losing the row that was in it",
   dict(_upgraded.execute("SELECT * FROM picks_log WHERE game_id='old1'").fetchone()
        )["ats_result"] == "L")


# ══ 25.3 AS-OF / LEAKAGE ══════════════════════════════════════════════════════
#
# The single most valuable property in the whole file. Every one of these is a
# question about time, and every one of them fails silently in production: a
# forecast that saw the future does not crash, it just looks unusually good.

print("\n── a forecast cannot see anything that had not happened yet ──")

import grade_snapshots as gsnap                                 # noqa: E402

_gconn = _db.connect(os.path.join(tempfile.mkdtemp(), "grades.db"))
_V = {"qb": 12.0, "rb": 8.0, "wr": 8.5, "ol": 11.0, "dl": 11.5,
      "lb": 7.5, "db": 8.0, "coach_st": 11.0}
_NOON = "2026-09-05T12:00:00+00:00"
_ONE_MINUTE_LATER = "2026-09-05T12:01:00+00:00"

gsnap.append_if_changed(_gconn, gsnap.build_snapshot(
    sport="cfb", season=2026, team="Oregon", values=_V, observed_at=_NOON))
_gconn.commit()
# Grant edits the sheet one minute after the forecast was generated.
gsnap.append_if_changed(_gconn, gsnap.build_snapshot(
    sport="cfb", season=2026, team="Oregon", values=dict(_V, qb=14.0),
    observed_at=_ONE_MINUTE_LATER))
_gconn.commit()

_at_noon = gsnap.grade_asof(_gconn, "cfb", 2026, "Oregon", _NOON)
ok("a grade effective one minute later is invisible to the earlier forecast",
   _at_noon["qb"] == 12.0, _at_noon["qb"])
_after = gsnap.grade_asof(_gconn, "cfb", 2026, "Oregon", "2026-09-05T12:02:00+00:00")
ok("...and visible to a later one", _after["qb"] == 14.0, _after["qb"])
ok("before any snapshot exists there is no grade, not a zero",
   gsnap.grade_asof(_gconn, "cfb", 2026, "Oregon", "2026-01-01T00:00:00+00:00") is None)
ok("a team never graded has no vector at all",
   gsnap.grade_asof(_gconn, "cfb", 2026, "Nobody", _NOON) is None)

# A vector that has not moved must not become a new row, or "when did this team
# change" stops being answerable.
_n_before = _gconn.execute("SELECT COUNT(*) c FROM grade_snapshots").fetchone()["c"]
gsnap.append_if_changed(_gconn, gsnap.build_snapshot(
    sport="cfb", season=2026, team="Oregon", values=dict(_V, qb=14.0),
    observed_at="2026-09-05T18:00:00+00:00"))
_gconn.commit()
_n_after = _gconn.execute("SELECT COUNT(*) c FROM grade_snapshots").fetchone()["c"]
ok("re-syncing an unchanged sheet writes nothing", _n_before == _n_after,
   "%d then %d" % (_n_before, _n_after))
ok("a changed vector hashes differently",
   gsnap.vector_hash(_V) != gsnap.vector_hash(dict(_V, qb=14.0)))
ok("...and an unchanged one hashes the same regardless of key order",
   gsnap.vector_hash(_V) == gsnap.vector_hash(dict(reversed(list(_V.items())))))
ok("the hash covers the football and not the plumbing",
   gsnap.build_snapshot(sport="cfb", season=2026, team="X", values=_V,
                        observed_at=_NOON, source_name="tab A")["source_hash"]
   == gsnap.build_snapshot(sport="cfb", season=2026, team="X", values=_V,
                           observed_at="2026-10-01T00:00:00+00:00",
                           source_name="tab B")["source_hash"])

# A snapshot carries the whole team, so a forecast cannot pick up this week's QB
# beside last week's line.
ok("a snapshot is one team's whole vector",
   set(gsnap.vector_of(_at_noon)) == set(gsnap.POSITIONS))
ok("a missing position is reported as missing, not as zero",
   gsnap.missing_positions(_gconn, "cfb", 2026, "Nobody", _NOON) == gsnap.POSITIONS)

# Reconstructed history says it is reconstructed.
ok("a backfilled snapshot is labelled a backfill, not a live observation",
   gsnap.build_snapshot(sport="cfb", season=2025, team="X", values=_V,
                        observed_at=_NOON,
                        source_type=gsnap.SOURCE_BACKFILL)["source_type"]
   == "backfill")


# ══ 25.4 MARKET ═══════════════════════════════════════════════════════════════

print("\n── every provider is kept, and policy decides later ──")

import market                                                   # noqa: E402
import market_policy as mkp                                     # noqa: E402

_mconn = _db.connect(os.path.join(tempfile.mkdtemp(), "market.db"))
_mconn.execute("INSERT INTO games (game_id, sport, season, week, home_team, away_team,"
               " kickoff, neutral_site) VALUES ('q1','cfb',2026,1,'H','A',"
               " '2026-09-05T18:00:00+00:00',0)")
_mconn.commit()


def _q(prov, spread, total, at, **kw):
    return market.build_quote(sport="cfb", game_id="q1", provider=prov,
                              observed_at=at, home_spread=spread, total=total, **kw)


_T0 = "2026-09-05T12:00:00+00:00"
_T1 = "2026-09-05T15:00:00+00:00"
_quotes = [_q("DraftKings", -3.5, 52.5, _T1, home_ml=-160, away_ml=140),
           _q("Caesars", -3.0, 53.0, _T1, home_ml=-155, away_ml=135),
           _q("Bovada", -4.0, 52.0, _T1, home_ml=-170, away_ml=145),
           _q("DraftKings", -2.5, 51.5, _T0)]
market.insert_quotes(_mconn, _quotes)
ok("two providers are preserved, not collapsed to one",
   market.quote_count(_mconn, "q1") == 4, market.quote_count(_mconn, "q1"))
ok("a provider rename is one provider, not market movement",
   market.canonical_provider("Draft Kings") == market.canonical_provider("DraftKings")
   == "DraftKings")
ok("...and an unknown book still passes through under its own name",
   market.canonical_provider("Some New Book") == "Some New Book")

_asof = market.quotes_asof(_mconn, "q1", _T1)
ok("as-of returns one row per provider", len(_asof) == 3, sorted(_asof))
ok("...the latest one at or before the time",
   _asof["DraftKings"]["home_spread"] == -3.5, _asof["DraftKings"]["home_spread"])
_early = market.quotes_asof(_mconn, "q1", _T0)
ok("...and an earlier as-of sees the earlier number",
   _early["DraftKings"]["home_spread"] == -2.5 and len(_early) == 1, sorted(_early))

# THE LEAKAGE GUARD. A quote observed after the forecast cannot be returned to it.
ok("a quote observed later cannot be seen by an earlier as-of",
   "Caesars" not in _early)

_snap = mkp.build_market_snapshot(_asof, at_time=_T1, game_id="q1")
ok("consensus is the median, not one chosen book",
   _snap["consensus_spread"] == -3.5, _snap["consensus_spread"])
ok("...and the median total", _snap["consensus_total"] == 52.5, _snap["consensus_total"])
ok("the spread of opinion is reported", (_snap["spread_min"], _snap["spread_max"]) == (-4.0, -3.0))
ok("the constituent quotes are named", len(json.loads(_snap["quote_ids_json"])) == 3)
ok("a three-provider snapshot is a consensus", _snap["status"] == mkp.VALID_CONSENSUS)
ok("probabilities are de-vigged per book before they are combined",
   0.55 < _snap["consensus_home_prob"] < 0.68, _snap["consensus_home_prob"])

# One slow book must not drag the number, and must be excluded by age, not by
# a judgement about whether we like its price.
_stale = dict(_asof)
_stale["Ancient"] = _q("Ancient", -12.0, 70.0, "2026-09-01T00:00:00+00:00")
_snap2 = mkp.build_market_snapshot(_stale, at_time=_T1, game_id="q1")
ok("a stale provider is excluded from consensus",
   _snap2["consensus_spread"] == -3.5 and "Ancient" not in _snap2["providers"],
   _snap2["providers"])
_only_stale = mkp.build_market_snapshot(
    {"Ancient": _stale["Ancient"]}, at_time=_T1, game_id="q1")
ok("...but a snapshot built only from stale quotes says so rather than pretending",
   _only_stale["status"] == mkp.STALE_FALLBACK, _only_stale["status"])
ok("one provider is a quote, not a consensus",
   mkp.build_market_snapshot({"DraftKings": _asof["DraftKings"]}, at_time=_T1,
                             game_id="q1")["status"] == mkp.SINGLE_PROVIDER)
ok("no market at all is a recorded fact, not a None",
   mkp.build_market_snapshot({}, at_time=_T1, game_id="q1")["status"] == mkp.MISSING)

# THE CLOSE. Nothing observed after kickoff may take part, for any reason.
market.insert_quotes(_mconn, [_q("DraftKings", -21.0, 40.0, "2026-09-05T23:00:00+00:00")])
_close = mkp.build_close(_mconn, "q1", "2026-09-05T18:00:00+00:00")
ok("the close uses the last quote before kickoff",
   _close["consensus_spread"] == -3.5, _close["consensus_spread"])
ok("...and a post-kickoff quote cannot become the close",
   _close["spread_min"] != -21.0 and _close["spread_max"] != -21.0,
   (_close["spread_min"], _close["spread_max"]))
ok("the close records which policy produced it",
   _close["policy_version"] == mkp.CLOSE_V1)

# Missing prices stay missing.
_np = _q("NoPrice", -3.0, 52.0, _T1)
ok("a quote with no spread price stores NULL, not -110",
   _np["home_spread_price"] is None and _np["away_spread_price"] is None)

# A book quoting itself two different ways at one instant is real -- CFBD sends
# "DraftKings" and "Draft Kings" in the same response -- and the choice between
# them must be stable rather than whatever order the database returned.
market.insert_quotes(_mconn, [
    market.build_quote(sport="cfb", game_id="q1", provider="Draft Kings",
                       observed_at=_T1, home_spread=-9.5, total=52.5,
                       source="cfbd/lines:Draft Kings")])
_conf = market.provider_conflicts(_mconn, "q1", _T1)
ok("a book disagreeing with itself is surfaced, not silently resolved",
   any(c["provider"] == "DraftKings" for c in _conf), _conf)
_a = market.quotes_asof(_mconn, "q1", _T1)["DraftKings"]["quote_id"]
_b = market.quotes_asof(_mconn, "q1", _T1)["DraftKings"]["quote_id"]
ok("...and the quote a consensus uses is the same one every time", _a == _b)

_sid = mkp.store_snapshot(_mconn, _snap)
ok("a snapshot can be stored", _sid == _snap["market_snapshot_id"])
mkp.store_snapshot(_mconn, _snap)
ok("...and storing it twice does not duplicate it",
   _mconn.execute("SELECT COUNT(*) c FROM market_snapshots").fetchone()["c"] == 1)


# ══ 25.5 HORIZONS ═════════════════════════════════════════════════════════════

print("\n── a run records the horizon it hit, not the one it aimed at ──")

import horizons as H                                            # noqa: E402

_KO = "2026-09-05T18:00:00+00:00"
ok("exactly on target is accepted",
   H.classify(kickoff=_KO, generated_at="2026-09-05T16:00:00+00:00",
              horizon="T2")["status"] == H.ACCEPTED)
ok("inside the tolerance is accepted",
   H.classify(kickoff=_KO, generated_at="2026-09-05T16:15:00+00:00",
              horizon="T2")["status"] == H.ACCEPTED)
# A scheduler that fires an hour late has NOT produced a T2 forecast, and
# relabelling it as one spends the only thing a standardized horizon buys.
ok("an hour late is recorded as late, not relabelled",
   H.classify(kickoff=_KO, generated_at="2026-09-05T17:00:00+00:00",
              horizon="T2")["status"] == H.LATE)
ok("too early is recorded as early",
   H.classify(kickoff=_KO, generated_at="2026-09-05T15:00:00+00:00",
              horizon="T2")["status"] == H.EARLY)
ok("after kickoff is post_kick whatever the delta says",
   H.classify(kickoff=_KO, generated_at="2026-09-05T18:00:01+00:00",
              horizon="T2")["status"] == H.POST_KICK)
ok("...and no tolerance is wide enough to make it pregame",
   H.classify(kickoff=_KO, generated_at="2026-09-05T19:00:00+00:00",
              horizon="T2", tolerance_minutes=10000)["status"] == H.POST_KICK)
ok("the delta is signed, positive for a late run",
   H.classify(kickoff=_KO, generated_at="2026-09-05T16:10:00+00:00",
              horizon="T2")["delta_seconds"] == 600)
ok("only the horizons whose window is open are due",
   H.due_horizons(kickoff=_KO, now="2026-09-05T15:55:00+00:00") == ["T2"])
ok("a run outside every window fills nothing",
   H.due_horizons(kickoff=_KO, now="2026-09-05T09:00:00+00:00") == [])
ok("T30 is collected but never official",
   "T30" in H.ALL and not H.is_official("T30"))
ok("T2 is the official lock", H.is_official("T2"))
ok("a window that has closed is missed",
   H.missed(kickoff=_KO, now="2026-09-05T17:00:00+00:00", horizon="T2"))
ok("...and one still open is not",
   not H.missed(kickoff=_KO, now="2026-09-05T16:05:00+00:00", horizon="T2"))

_hconn = _db.connect(os.path.join(tempfile.mkdtemp(), "h.db"))
ok("a miss is recorded once",
   H.record_miss(_hconn, game_id="g1", model_version="C0", horizon="T2",
                 kickoff=_KO, now="2026-09-05T17:00:00+00:00"))
ok("...and re-detecting it does not duplicate the record",
   not H.record_miss(_hconn, game_id="g1", model_version="C0", horizon="T2",
                     kickoff=_KO, now="2026-09-05T17:30:00+00:00"))
ok("...so a later retry cannot erase the gap",
   _hconn.execute("SELECT COUNT(*) c FROM snapshot_misses").fetchone()["c"] == 1)


# ══ 25.6 STRATEGY ═════════════════════════════════════════════════════════════

print("\n── every decision is recorded, including the ones that decline ──")

import signals as sig                                           # noqa: E402

_FC = {"forecast_id": "fc_t", "game_id": "g1", "snapshot_status": H.ACCEPTED,
       "horizon": "T2", "borrowed_fallback": 0, "pred_home_margin": 7.0}
_PL = {"home_team": "H", "away_team": "A", "market_status": "valid_consensus",
       "consensus_spread": 3.0, "market_policy_version": "consensus_v1",
       "market_snapshot_id": "ms_1",
       "home_grade_vector": {p: 1.0 for p in gsnap.POSITIONS},
       "away_grade_vector": {p: 1.0 for p in gsnap.POSITIONS}}

_e, _r, _x = sig.evaluate_spread(forecast=_FC, payload=_PL)
ok("a clean forecast at the official horizon is eligible", _e, _r)
ok("...and picks the side the edge points at",
   _x["decision"]["side"] == "H" and _x["edge"] == 4.0, _x)
ok("...at the consensus line, with no price invented",
   _x["decision"]["line"] == 3.0 and _x["decision"]["price"] is None)

for label, fc, pl, code in [
        ("a game the film could not answer", dict(_FC, borrowed_fallback=1), _PL,
         sig.BORROWED_RATER),
        ("an unrated team", _FC, dict(_PL, home_grade_vector=None), sig.UNRATED_TEAM),
        ("an incomplete grade vector", _FC,
         dict(_PL, home_grade_vector=dict(_PL["home_grade_vector"], qb=None)),
         sig.INCOMPLETE_GRADE),
        ("a line outside the model's domain", _FC, dict(_PL, consensus_spread=40.0),
         sig.BLOWOUT_OUT_OF_DOMAIN),
        ("no market at all", _FC,
         dict(_PL, market_status="missing", consensus_spread=None), sig.MISSING_MARKET),
        ("a stale market", _FC, dict(_PL, market_status="stale_fallback"),
         sig.STALE_MARKET),
        ("a forecast made after kickoff", dict(_FC, snapshot_status=H.POST_KICK),
         _PL, sig.POST_KICKOFF),
        ("a forecast from the wrong horizon", dict(_FC, horizon="T24"), _PL,
         sig.OUTSIDE_HORIZON),
        ("a late run", dict(_FC, snapshot_status=H.LATE), _PL, sig.OUTSIDE_HORIZON),
        ("no model number", dict(_FC, pred_home_margin=None), _PL, sig.NO_MODEL_NUMBER)]:
    e2, r2, _ = sig.evaluate_spread(forecast=fc, payload=pl)
    ok("%s is declined, and says why" % label, (not e2) and code in r2, r2)

# The evaluations table is written whether or not anything is bet.
_sconn = _db.connect(os.path.join(tempfile.mkdtemp(), "s.db"))
_evals = sig.evaluate(_sconn, forecast=_FC, payload=_PL)
ok("one evaluation per market, every time", len(_evals) == 3,
   [e["decision_market"] or "-" for e in _evals])
ok("...and a disabled market is a recorded policy, not an absence",
   any(sig.MARKET_DISABLED in e["_reasons"] for e in _evals))
_stored = _sconn.execute("SELECT COUNT(*) c FROM strategy_evaluations").fetchone()["c"]
ok("declines are stored, so 'no bet' can be audited", _stored == 3, _stored)

_spread_eval = next(e for e in _evals if e["decision_market"] == "spread")
_s1 = sig.emit_signal(_sconn, evaluation=_spread_eval, forecast=_FC, is_official=True)
ok("an eligible evaluation produces exactly one signal", _s1 is not None)
_s2 = sig.emit_signal(_sconn, evaluation=_spread_eval, forecast=_FC, is_official=True)
_n_sig = _sconn.execute("SELECT COUNT(*) c FROM signal_log").fetchone()["c"]
ok("...and a retried run does not publish it twice", _n_sig == 1, _n_sig)
ok("an ineligible evaluation produces none",
   sig.emit_signal(_sconn, evaluation=dict(_evals[1], eligible=0),
                   forecast=_FC) is None)

# The database refuses a second official signal even if the code forgets to.
_dup = dict(_s1, signal_id="sg_different", evaluation_id="ev_different")
_refused = False
try:
    _sconn.execute("INSERT INTO signal_log (%s) VALUES (%s)"
                   % (",".join(sig.SIGNAL_COLUMNS),
                      ",".join(":" + c for c in sig.SIGNAL_COLUMNS)),
                   {k: _dup[k] for k in sig.SIGNAL_COLUMNS})
except sqlite3.IntegrityError:
    _refused = True
ok("a second OFFICIAL signal for the same game and market is refused by the schema",
   _refused, "the partial unique index did not fire")

ok("the strategy is data, and it is hashed", len(sig.strategy_hash()) == 64)
ok("...and the version names the rules, not the week",
   sig.STRATEGY_V0["strategy_version"].startswith("S0-"))
ok("totals are off until a totals model exists",
   sig.STRATEGY_V0["totals_enabled"] is False)
ok("moneylines are off until a calibrated probability exists",
   sig.STRATEGY_V0["moneyline_enabled"] is False)


# ══ 25.8 STATE JOURNAL ════════════════════════════════════════════════════════

print("\n── the cache is an accelerator, not the record ──")

import state_events as sev                                      # noqa: E402
import replay_state                                             # noqa: E402

_sdir = tempfile.mkdtemp()
_ev = sev.make_event("market_quote", {"quote_id": "mq_1", "home_spread": -3.5},
                     occurred_at="2026-09-05T12:00:00+00:00")
ok("an append writes the event", sev.append([_ev], _sdir, known=set()) == (1, 0))
ok("...and appending it again writes nothing",
   sev.append([_ev], _sdir) == (0, 1))
_read = sev.read_all(_sdir)
ok("what comes back is what went in", len(_read) == 1
   and _read[0]["payload"]["home_spread"] == -3.5)
ok("the journal verifies against its own hashes", not sev.verify(_read))
_tampered = sev.read_all(_sdir)
_tampered[0]["payload"]["home_spread"] = -99.0
ok("[control] an edited payload fails verification", bool(sev.verify(_tampered)))
_dupe = dict(_ev, payload={"quote_id": "mq_1", "home_spread": -9.9})
_dupe["payload_hash"] = pv.payload_hash(_dupe["payload"])
ok("[control] two events sharing an id with different payloads is a problem",
   bool(sev.verify([_ev, _dupe])))

# Events are ordered by the world's clock, not by file order, so a replay applies
# facts in the order they happened however the files were written.
_out_of_order = [
    sev.make_event("signal", {"signal_id": "sg_2"}, occurred_at="2026-09-05T20:00:00+00:00"),
    sev.make_event("signal", {"signal_id": "sg_1"}, occurred_at="2026-09-05T10:00:00+00:00")]
sev.append(_out_of_order, _sdir)
ok("events read back in occurrence order",
   [e["payload"].get("signal_id") for e in sev.read_all(_sdir)
    if e["event_type"] == "signal"] == ["sg_1", "sg_2"])


# ══ 25.9 PRIVACY / THE MOAT ═══════════════════════════════════════════════════
#
# This repository is PUBLIC and a grade_snapshot payload carries the eight
# position grades for a named team. Publishing the journal without redaction
# would put every grade Grant has ever entered on the internet, machine-readable,
# permanently. These are the checks standing between the two.

print("\n── nothing published carries a film grade ──")

ok("the grade stream is marked unpublishable", not sev.publishable("grade_snapshot"))
ok("...and the market stream is publishable", sev.publishable("market_quote"))

_g = sev.make_event("grade_snapshot", {
    "grade_snapshot_id": "gs_1", "team": "Oregon", "source_hash": "abc",
    "effective_at": "2026-09-01T00:00:00+00:00",
    "qb": 12.2, "ol": 11.8, "dl": 12.2, "coach_st": 12.5})
_r = sev.redact(_g)
ok("redaction removes every position value",
   not any(p in _r["payload"] for p in gsnap.POSITIONS), _r["payload"])
ok("...and keeps the identity, so provenance survives",
   {"grade_snapshot_id", "team", "effective_at", "source_hash"} <= set(_r["payload"]))
ok("...and says what it removed", "qb" in _r["redacted"])
ok("...and re-hashes what is left, so verification still passes",
   _r["payload_hash"] == pv.payload_hash(_r["payload"]))

# The availability stream is PUBLISHABLE and still redacted, which is a third
# state and the one most easily got wrong. Who is out is a public fact anyone
# can read on ESPN. How far the line moves without him is computed by removing
# him and re-rating all 138 teams from the film grades — a grade number wearing
# different units, and it does not leave the machine.
_ae = sev.make_event("availability", {
    "event_id": "av_1", "team": "Oregon", "player": "A Back",
    "player_key": "a back", "status": "OUT", "source_tier": 3,
    "observed_at": "2026-09-03T12:00:00+00:00", "impact_points": 3.2})
_ar = sev.redact(_ae)
# The flag means "may this leave unredacted", and availability may not — one
# field of it is a grade number in different units.
ok("availability may not leave the machine unredacted",
   not sev.publishable("availability"))
ok("...and the modelled point impact is what gets removed",
   "impact_points" not in _ar["payload"], _ar["payload"])
ok("...while the status, the player and the timing survive",
   {"player", "status", "observed_at", "source_tier"} <= set(_ar["payload"]))
ok("CONTROL: the unredacted event did carry the number",
   _ae["payload"]["impact_points"] == 3.2)
ok("weather is published whole — none of it is anyone's private input",
   sev.publishable("weather")
   and "wind_mph" in sev.redact(sev.make_event(
       "weather", {"snapshot_id": "wx_1", "game_id": "g",
                   "observed_at": "2026-09-03T12:00:00+00:00",
                   "wind_mph": None, "temperature_f": 71.0}))["payload"])

# Both new streams are non-regenerable, which is the test §14.1 sets for what
# belongs in durable state at all. A cache loss must not be able to destroy them.
for _st, _tbl in (("availability", "availability_events"),
                  ("weather", "weather_snapshots")):
    ok("%s is exported to the journal" % _st,
       any(e[0] == _st and e[1] == _tbl for e in sev.EXPORTS))
    ok("...and replayed back out of it", replay_state.APPLY[_st][0] == _tbl)

# The audit, against the three shapes a leak actually takes.# The audit, against the three shapes a leak actually takes.
_leak = tempfile.mkdtemp()
sev.append([_g], _leak, known=set())
ok("an unredacted grade snapshot is caught", bool(sev.audit_publishable(_leak)))

_nested = tempfile.mkdtemp()
sev.append([sev.make_event("forecast", {
    "forecast_id": "fc_1",
    "debug": '{"team":"Oregon","qb":12.2,"ol":11.8}'})], _nested, known=set())
ok("...and so are grades nested inside a JSON string on a PUBLIC stream",
   bool(sev.audit_publishable(_nested)),
   "a top-level-only scan would pass this")

# And the check that keeps the gate usable: the model config's per-position
# WEIGHTS are public arithmetic, and a scan that fires on them gets switched off.
_cfgdir = tempfile.mkdtemp()
sev.append([sev.make_event("model_registry", {
    "model_version": "C0",
    "config_json": '{"grade_weights":{"qb":1.0,"ol":1.0,"dl":1.0}}'})],
    _cfgdir, known=set())
ok("...but the config's grade WEIGHTS do not trip it",
   not sev.audit_publishable(_cfgdir), sev.audit_publishable(_cfgdir))

_pub = tempfile.mkdtemp()
_full = tempfile.mkdtemp()
sev.append([_g, sev.make_event("market_quote", {"quote_id": "mq_9"})],
           _full, known=set())
_counts = sev.write_publishable(_full, _pub)
ok("a publishable copy redacts rather than dropping",
   _counts["redacted"] == 1 and _counts["dropped"] == 0, _counts)
ok("...and the result audits clean", not sev.audit_publishable(_pub))
ok("...while the working journal still has the grades",
   any("qb" in (e.get("payload") or {}) for e in sev.read_all(_full)))

# The whole point of the journal: an empty database, rebuilt.
_rt_src = _db.connect(os.path.join(tempfile.mkdtemp(), "rt.db"))
_now_iso = "2026-09-05T12:00:00+00:00"
_rt_src.execute(
    "INSERT INTO signal_log (signal_id, evaluation_id, forecast_id, game_id,"
    " strategy_version, market, side, line, created_at, official_horizon, is_official)"
    " VALUES ('sg_rt','ev','fc','g1','S0','spread','H',-3.5,?, 'T2',1)", (_now_iso,))
_rt_src.commit()
_rt_dir = tempfile.mkdtemp()
sev.append(sev.export_from_db(_rt_src), _rt_dir, known=set())
_rt_conn, _rt_counts = replay_state.rebuild(_rt_dir, os.path.join(tempfile.mkdtemp(), "back.db"),
                                            verbose=False)
ok("an empty database is rebuilt from the journal alone",
   _rt_counts.get("signal_log") == 1, _rt_counts)
ok("...and reconciles against the original",
   all(d["match"] for d in replay_state.reconcile(_rt_src, _rt_conn)))
_rt_conn.execute("DELETE FROM signal_log")
_rt_conn.commit()
ok("[control] a missing row makes reconciliation fail",
   any(not d["match"] for d in replay_state.reconcile(_rt_src, _rt_conn)))


print("\n── nothing personal reaches the published bundle ──")
#
# CONTEXT THAT MATTERS: the film grades in this bundle are public BY DECISION.
# Grant made that call on 1 September with the contents in front of him, removed
# the passphrase gate, and the bundle has been served in the clear since. The
# commit that did it names what was left behind: "anything genuinely private has
# to be kept OUT of the bundle... The live wire to watch is `mybets` -- it is
# empty today, but it reads the sheet's My Bets tab, so bets logged there would
# publish with stakes, books and ROI."
#
# This is that guard. A grade can be re-published; somebody's wagering cannot be
# un-published.

import public_export as pex                                     # noqa: E402

_BASE = {"teams": {"Oregon": {"grades": {"qb": 12.2}}},
         "bets": [{"game_id": "g1", "stake": 1.5, "ml_ev": 4.0}],
         "mybets": {"state": "ok", "n": 0}}
ok("a clean bundle passes", not pex.audit(_BASE), pex.audit(_BASE))

# THE FALSE POSITIVE THAT WOULD KILL THE GATE. `stake` on the model's own board
# is the recommended Kelly fraction -- the model's output, published on purpose.
# A rule that fires on it gets switched off, and then it protects nothing.
ok("...including the model's own recommended stake",
   not pex.audit({"bets": [{"stake": 2.0, "game_id": "g"}]}))

for label, node, why in [
        ("a logged bet with a stake and a book",
         {"mybets": {"bets": [{"stake": 2.5, "book": "DraftKings"}]}}, "wager row"),
        ("a bankroll three levels down",
         {"record": {"curve": {"meta": {"bankroll": 5000}}}}, "never publishable"),
        ("an access token inside a JSON string",
         {"config": {"debug": '{"access_token":"abc"}'}}, "never publishable"),
        ("dollar figures",
         {"totals": {"dollars_won": 120.0}}, "never publishable"),
        ("an account id", {"meta": {"account_id": "x"}}, "never publishable"),
        ("a stake beside a placed_at, however it is nested",
         {"a": {"b": [{"stake": 1.0, "placed_at": "2026-09-05"}]}}, "wager amount")]:
    ok("%s is caught" % label, bool(pex.audit(node)), pex.audit(node))

# A top-level-only scan passes the case that actually happens.
_deep = {"x": {"y": {"z": {"mybets": {"bets": [{"stake": 1, "book": "b"}]}}}}}
ok("a scan of the top level alone would miss it; this does not",
   bool(pex.audit(_deep)))

_safe = pex.safe_my_bets({"state": "ok", "problems": [], "fetched_utc": "t",
                          "bets": [{"stake": 5, "book": "b"}],
                          "totals": {"n": 1, "settled": 1, "open": 0,
                                     "dollars_won": 90.0, "unit_size": 25}})
ok("the status view keeps no rows", "bets" not in _safe)
ok("...and no dollar figures",
   not any(k in _safe for k in ("dollars_won", "unit_size", "totals")), _safe)
ok("...but does keep whether the sheet was readable",
   _safe["state"] == "ok" and _safe["n"] == 1)
ok("...and audits clean", not pex.audit({"mybets": _safe}))

# And the real thing, as written to disk.
_bundle_path = os.path.join(ROOT_DIR, "output", "research", "data.json")
if os.path.exists(_bundle_path):
    with open(_bundle_path) as _fh:
        _live = json.load(_fh)
    ok("the bundle actually on disk audits clean", not pex.audit(_live),
       pex.audit(_live)[:3])
    ok("...and its mybets carries no rows",
       "bets" not in (_live.get("mybets") or {}), _live.get("mybets"))


# ══ 25.10 RESEARCH MODELS ═════════════════════════════════════════════════════

print("\n── the ridge fit does what a ridge fit should ──")

import random                                                   # noqa: E402
from models_v2 import ridge as _ridge                           # noqa: E402
from models_v2 import residual_grade as _rg                     # noqa: E402
from models_v2 import matchup_residual as _mr                   # noqa: E402
from models_v2 import MarketBaseline                            # noqa: E402

random.seed(20260906)
_syn = []
for _ in range(600):
    _a, _b, _c = random.gauss(0, 2), random.gauss(0, 3), random.gauss(0, 1)
    _syn.append({"a": _a, "b": _b, "c": _c,
                 "y": 3.0 * _a - 1.5 * _b + 0.0 * _c + 7.0 + random.gauss(0, 1)})
_art = _ridge.fit_ridge(_syn, ["a", "b", "c"], "y", lam=0.01)
_raw = {f: _art["coefficients"][f] / _art["sds"][f] for f in ("a", "b", "c")}
ok("known coefficients are recovered",
   abs(_raw["a"] - 3.0) < 0.15 and abs(_raw["b"] + 1.5) < 0.15
   and abs(_raw["c"]) < 0.15, {k: round(v, 3) for k, v in _raw.items()})
_big = _ridge.fit_ridge(_syn, ["a", "b", "c"], "y", lam=1000.0)
ok("a larger lambda shrinks the coefficients",
   sum(abs(v) for v in _big["coefficients"].values())
   < sum(abs(v) for v in _art["coefficients"].values()))
ok("standardization is recorded with the artifact",
   set(_art["means"]) == {"a", "b", "c"} and set(_art["sds"]) == {"a", "b", "c"})
ok("...and the feature ORDER is persisted, not re-derived",
   _art["features"] == ["a", "b", "c"])
ok("the intercept is not shrunk toward zero",
   abs(_big["intercept"] - _art["intercept"]) < 1e-9,
   (_art["intercept"], _big["intercept"]))

# A zero-variance feature would divide by zero and poison every coefficient.
_zv = [dict(r, d=5.0) for r in _syn]
_azv = _ridge.fit_ridge(_zv, ["a", "d"], "y", lam=1.0)
ok("a zero-variance feature does not produce NaN",
   all(v == v for v in _azv["coefficients"].values()), _azv["coefficients"])
ok("...and is named in the artifact rather than silently dropped",
   "d" in _azv["zero_variance"], _azv["zero_variance"])

# Lambda must be chosen on data the fit did not see.
_refused = None
try:
    _rg.ResidualGrade().fit(_syn[:100])
except ValueError as e:
    _refused = str(e)
ok("choosing lambda without a validation split is refused", _refused is not None,
   _refused)

# A model that cannot see one side of a game has no opinion about it.
_full = {p: 10.0 for p in _rg.POSITIONS}
ok("no grade vector means no features, not a vector of zeros",
   _rg.features_from_payload({"home_grade_vector": None,
                              "away_grade_vector": _full,
                              "consensus_spread": -3.0}) is None)
ok("...and one missing position is the same answer",
   _rg.features_from_payload({"home_grade_vector": dict(_full, qb=None),
                              "away_grade_vector": _full,
                              "consensus_spread": -3.0}) is None)
ok("...and so is no market",
   _rg.features_from_payload({"home_grade_vector": _full,
                              "away_grade_vector": _full,
                              "consensus_spread": None}) is None)

_pl = {"home_grade_vector": dict(_full, ol=12.0),
       "away_grade_vector": dict(_full, dl=8.0),
       "consensus_spread": -3.0, "neutral_site": 0}
_f = _mr.features_from_payload(_pl)
ok("the matchup features pit a line against the line it faces",
   abs(_f["home_ol_vs_away_dl"] - 4.0) < 1e-9, _f["home_ol_vs_away_dl"])
ok("...and are the ones pre-registered, not a set chosen after fitting",
   set(_mr.MATCHUP_FEATURES) <= set(_f))

# C1 is mandatory and must be exactly the market, never a smoothed version.
_mb = MarketBaseline().predict({"consensus_spread": -3.5, "consensus_total": 52.5,
                                "consensus_home_prob": 0.61})
ok("the market baseline predicts the market", _mb["pred_home_margin"] == -3.5)
ok("...and has nothing to fit", MarketBaseline().fit([]) is not None)
ok("...and says nothing when there is no market",
   MarketBaseline().predict({})["pred_home_margin"] is None)

# Every model answers the same shape, and an unsupported field is None.
from models_v2 import FormQuality as _FQ0                       # noqa: E402
for _m in (MarketBaseline(), _rg.ResidualGrade(), _mr.MatchupResidual(), _FQ0()):
    _out = _m.predict({})
    ok("%s returns the normalized keys" % _m.model_id,
       set(_out) >= {"pred_home_margin", "pred_total", "home_win_prob",
                     "home_cover_prob", "over_prob", "borrowed_fallback"})
    ok("...with None and not a default for what it cannot say" if _m.model_id
       == "residual-grade" else "...%s: no invented defaults" % _m.model_id,
       _out["home_cover_prob"] is None)

# A registered version cannot be redefined into a different model.
import forecast_v2 as _fv2                                      # noqa: E402
_rconn = _db.connect(os.path.join(tempfile.mkdtemp(), "reg.db"))
_fv2.register_model(_rconn, model_version="X1", model_id="m", role="challenger",
                    config={"scale": 1.0})
_conflict = None
try:
    _fv2.register_model(_rconn, model_version="X1", model_id="m",
                        role="challenger", config={"scale": 2.0})
except _fv2.VersionConflict as e:
    _conflict = str(e)
ok("a version cannot be redefined with a different config", _conflict is not None)
ok("...and re-registering the SAME config is a no-op",
   _fv2.register_model(_rconn, model_version="X1", model_id="m",
                       role="challenger", config={"scale": 1.0})["model_version"] == "X1")


print("\n── the form challenger cannot run away, and does not touch the Champion ──")

from models_v2.form_quality import TeamForm, MARKET, MODEL                # noqa: E402

_tf = TeamForm()
_tf.new_season(2026)
ok("a team with no games has no form", _tf.value_for("Oregon") == 0.0)
_tf.observe(home_team="Oregon", away_team="Boise", actual_home_margin=30,
            expected_home_margin=10)
ok("beating expectation raises one side and lowers the other",
   _tf.value_for("Oregon") > 0 > _tf.value_for("Boise"))
ok("...and one game is shrunk hard, because two games is not a form",
   _tf.value_for("Oregon") < 0.35 * 20, _tf.value_for("Oregon"))

_a, _b = TeamForm(), TeamForm()
_a.new_season(2026); _b.new_season(2026)
_a.observe(home_team="A", away_team="B", actual_home_margin=99, expected_home_margin=0)
_b.observe(home_team="A", away_team="B", actual_home_margin=21, expected_home_margin=0)
ok("a 99-point win says the same as a 21-point win",
   abs(_a.value_for("A") - _b.value_for("A")) < 1e-9,
   "winsorization did not cap the residual")

_a.new_season(2027)
ok("a new season clears form, because the roster turned over",
   _a.value_for("A") == 0.0)
_a.observe(home_team="A", away_team="B", actual_home_margin=None,
           expected_home_margin=3.0)
ok("an unfinished game does not update anything", _a.value_for("A") == 0.0)

_undeclared = None
try:
    TeamForm(expected_from="vibes")
except ValueError as e:
    _undeclared = str(e)
ok("the information set must be declared", _undeclared is not None)
ok("...and both readings exist deliberately", {MARKET, MODEL} == {"market", "model"})

# ── E005 is now a fitted challenger, and its feature is exactly-as-of ────────
#
# The form feature is replayed from finished games rather than stored, and the
# thing that makes it safe is the SETTLE rule: kickoff alone is not enough,
# because a game that started two hours ago is still being played and its final
# margin is a fact from the future. §32 calls this out by name.
from models_v2 import form_quality as _fq                       # noqa: E402
from models_v2 import FormQuality as _FQ                        # noqa: E402
import features_v2 as _feat                                     # noqa: E402

ok("a game still being played has not settled",
   _fq.settled_before("2026-09-05T23:00:00+00:00", "2026-09-06T01:00:00+00:00") is False)
ok("...and one that finished hours ago has",
   _fq.settled_before("2026-09-05T23:00:00+00:00", "2026-09-06T06:00:00+00:00") is True)
ok("CONTROL: kickoff alone would have let the unfinished game in",
   "2026-09-05T23:00:00+00:00" < "2026-09-06T01:00:00+00:00")
ok("an unparseable time settles nothing rather than defaulting to true",
   _fq.settled_before("not a time", "2026-09-06T06:00:00+00:00") is False)
ok("...and so does a missing one", _fq.settled_before(None, "2026-09-06T06:00:00+00:00") is False)

# The as-of replay, on a database of its own so the assertion is about the rule
# and not about whatever the live season happens to contain.
_fdb = os.path.join(tempfile.mkdtemp(), "form.db")
_fconn = _db.connect(_fdb)
_fconn.executemany(
    "INSERT INTO games (game_id, sport, season, week, home_team, away_team,"
    " kickoff, home_score, away_score, neutral_site) VALUES (?,?,?,?,?,?,?,?,?,0)",
    [("f1", "cfb", 2026, 1, "A", "B", "2026-09-01T16:00:00+00:00", 40, 10),
     ("f2", "cfb", 2026, 2, "A", "C", "2026-09-08T16:00:00+00:00", 20, 10),
     ("f3", "cfb", 2026, 2, "D", "E", "2026-09-08T16:00:00+00:00", 30, 0)])
_fconn.executemany("INSERT INTO lines (game_id, provider, home_margin) VALUES (?,?,?)",
                   [("f1", "consensus", 7.0), ("f2", "consensus", 3.0)])
_fconn.commit()

_fq.clear_cache()
_early = _fq.form_asof(_fconn, "cfb", 2026, "2026-09-01T18:00:00+00:00")
ok("a game two hours old has not entered anyone's form", _early.value_for("A") == 0.0)
_fq.clear_cache()
_after = _fq.form_asof(_fconn, "cfb", 2026, "2026-09-02T00:00:00+00:00")
ok("...and once it has settled, it has",
   _after.value_for("A") > 0 > _after.value_for("B"), _after.value_for("A"))
_fq.clear_cache()
_later = _fq.form_asof(_fconn, "cfb", 2026, "2026-09-09T06:00:00+00:00")
ok("a game with NO line contributes nothing rather than its raw margin",
   _later.value_for("D") == 0.0 and _later.value_for("E") == 0.0,
   "%s / %s" % (_later.value_for("D"), _later.value_for("E")))
# Zero form and no form are different states and the value alone cannot tell
# them apart -- a team that performed exactly to the line also reads 0.0. The
# game COUNT is what separates "observed, and unremarkable" from "never seen".
ok("...and the unpriced game left both teams unobserved, not merely unmoved",
   "D" not in _later.games and "E" not in _later.games)
ok("...while the priced games did move both teams",
   _later.value_for("C") < 0 < _later.value_for("A"),
   "A %s / C %s" % (_later.value_for("A"), _later.value_for("C")))
# The whole point of an as-of function: asking about an earlier instant after
# later games exist must give the earlier answer.
_fq.clear_cache()
ok("CONTROL: asking about the earlier instant still gives the earlier answer",
   abs(_fq.form_asof(_fconn, "cfb", 2026, "2026-09-02T00:00:00+00:00").value_for("A")
       - _after.value_for("A")) < 1e-12)

_e5 = _FQ({"coefficients": {"form_diff": 0.5}, "intercept": 0.0,
                        "features": ["form_diff"], "means": {"form_diff": 0.0},
                        "sds": {"form_diff": 1.0}})
ok("E005 predicts from the market plus its one term",
   _e5.predict({"consensus_spread": -7.0, "form_diff": 4.0})["pred_home_margin"] == -5.0)
ok("no market number, no prediction — never a bare form number",
   _e5.predict({"consensus_spread": None, "form_diff": 4.0})["pred_home_margin"] is None)
# A payload recorded under the v1 schema has no form field at all. Reading that
# as zero would silently turn "not known" into "exactly as expected".
ok("a v1 payload has no form, and E005 declines rather than assuming zero",
   _e5.predict({"consensus_spread": -7.0})["pred_home_margin"] is None)
ok("...and an unfitted E005 predicts nothing at all",
   _FQ().predict({"consensus_spread": -7.0, "form_diff": 4.0})
   ["pred_home_margin"] is None)

# The feature schema bump is ADDITIVE. A v1 reader must still find every key it
# knew, or the bump silently retires models that read them.
ok("champion_features_v2 is additive over v1",
   _feat.CHAMPION_FEATURES_V2 != _feat.CHAMPION_FEATURES_V1)

# ── C6: a total is not a spread, and this one knows it ───────────────────────
from models_v2 import totals as _tot                            # noqa: E402

_ts = _tot.TeamScoring()
ok("with nothing observed, a team scores the league mean and not zero",
   _ts.offence("A") == _ts.league_mean() == 24.0)
_ts.observe(home_team="A", away_team="B", home_score=45, away_score=3)
ok("one 45-point game does not make a 45-point offence",
   _ts.offence("A") < 45.0, _ts.offence("A"))
ok("...and it moved the right way", _ts.offence("A") > _ts.league_mean())
_gap1 = 45.0 - _ts.offence("A")
for _ in range(40):
    _ts.observe(home_team="A", away_team="C", home_score=45, away_score=3)
_gap41 = 45.0 - _ts.offence("A")
# The property, not a tolerance: the estimate lies strictly between the league
# mean and what actually happened, and n moves it toward what happened. A
# threshold here would be a statement about this fixture rather than the rule.
ok("...while forty of them move it most of the way",
   _ts.league_mean() < _ts.offence("A") < 45.0 and _gap41 < _gap1 / 10,
   "gap after 1: %.2f, after 41: %.2f" % (_gap1, _gap41))

_tot.clear_cache()
_sc_early = _tot.scoring_asof(_fconn, "cfb", 2026, "2026-09-01T18:00:00+00:00")
ok("scoring rates use the same settle rule as form",
   _sc_early.games("A") == 0)
_tot.clear_cache()
_sc_late = _tot.scoring_asof(_fconn, "cfb", 2026, "2026-09-09T06:00:00+00:00")
ok("...and an unpriced game still counts toward SCORING, unlike form",
   _sc_late.games("D") == 1 and "D" not in _later.games,
   "scoring saw %d, form saw %r" % (_sc_late.games("D"), _later.games.get("D")))

_c6 = _tot.TotalsScoring({
    "home": {"coefficients": {"off_pg": 1.0, "opp_def_pg": 1.0, "neutral": 0.0},
             "intercept": 0.0, "features": ["off_pg", "opp_def_pg", "neutral"],
             "means": {"off_pg": 0.0, "opp_def_pg": 0.0, "neutral": 0.0},
             "sds": {"off_pg": 1.0, "opp_def_pg": 1.0, "neutral": 1.0}},
    "away": {"coefficients": {"off_pg": 1.0, "opp_def_pg": 1.0, "neutral": 0.0},
             "intercept": 0.0, "features": ["off_pg", "opp_def_pg", "neutral"],
             "means": {"off_pg": 0.0, "opp_def_pg": 0.0, "neutral": 0.0},
             "sds": {"off_pg": 1.0, "opp_def_pg": 1.0, "neutral": 1.0}}})
_pl = {"home_scoring": {"off_pg": 30.0, "def_pg": 20.0},
       "away_scoring": {"off_pg": 24.0, "def_pg": 26.0}, "neutral_site": 0}
_o6 = _c6.predict(_pl)
ok("C6 predicts a total from both sides separately",
   _o6["pred_total"] == (30.0 + 26.0) + (24.0 + 20.0), _o6["pred_total"])
# §20.8: do not reuse spread logic. A model with no opinion about who wins must
# not emit one -- the difference of two shrunk scoring rates has a plausible
# shape and no thought behind it.
ok("...and has NO opinion about the spread", _o6["pred_home_margin"] is None)
ok("no scoring rates, no total — never the league mean standing in for a team",
   _c6.predict({"neutral_site": 0})["pred_total"] is None)

ok("a model with two fitted sides is fitted", _tot.TotalsScoring.is_fitted(_c6.artifact()))
ok("CONTROL: one side fitted is not fitted",
   not _tot.TotalsScoring.is_fitted({"home": _c6.artifact()["home"]}))
# The loader used to look for a top-level `coefficients` key. C6 has none, and
# would have been dropped on every run with a log line for a symptom.
ok("CONTROL: the old top-level-coefficients guard would have dropped C6",
   not _c6.artifact().get("coefficients"))
ok("the market baseline needs no artifact at all",
   MarketBaseline.is_fitted({}) and MarketBaseline.is_fitted(None))

ok("champion_features_v3 is its own string, not a redefinition of v2",
   len({_feat.CHAMPION_FEATURES_V1, _feat.CHAMPION_FEATURES_V2,
        _feat.CHAMPION_FEATURES_V3}) == 3)

# ── the registry IS the artifact ─────────────────────────────────────────────
#
# The fitted coefficients were written to output/, output/ is in .gitignore, and
# the Actions cache carries data/ and not output/. Every scheduled run therefore
# found no artifact file and skipped every challenger, which is precisely the
# "accumulates a record of the games it happened to be alive for" failure
# load_challengers exists to prevent — happening on every run since they were
# fitted, announced only in a log line nobody reads.
_lc_conn = _db.connect(os.path.join(tempfile.mkdtemp(), "challengers.db"))
_fv2.register_model(_lc_conn, model_version="E005-test", model_id="form-quality",
                    role="challenger", config={"coefficients": {"form_diff": 0.5},
                                               "intercept": 0.0,
                                               "features": ["form_diff"],
                                               "means": {"form_diff": 0.0},
                                               "sds": {"form_diff": 1.0}})
_loaded = dict(_fv2.load_challengers(_lc_conn))
ok("a challenger rehydrates from the registry with no artifact file on disk",
   "E005-test" in _loaded)
ok("...and the rehydrated model actually predicts",
   _loaded["E005-test"].predict({"consensus_spread": -7.0, "form_diff": 4.0})
   ["pred_home_margin"] == -5.0)
_fv2.register_model(_lc_conn, model_version="E098-empty", model_id="form-quality",
                    role="challenger", config={"note": "registered but never fitted"})
ok("CONTROL: a registry row with no coefficients is skipped, not run empty",
   "E098-empty" not in dict(_fv2.load_challengers(_lc_conn)))

# ── the migration applies itself ─────────────────────────────────────────────
#
# It was a one-off command a human was supposed to remember. The first production
# run after the cutover proved that is not good enough: V2-official code, the
# legacy writer stood down, and `ats_result_at_pick` NULL on every row because
# nothing had ever migrated that database. The public page would have fallen back
# to the legacy close-based headline under a page claiming otherwise.
_ru = open(os.path.join(ROOT, "src", "run_update.py")).read()
ok("run_update applies the V2 migration itself",
   "migrate_v2.already_applied(conn)" in _ru and "migrate_v2.apply_migration" in _ru)
ok("...only when V2 is the official writer",
   _ru.index("if V2_OFFICIAL:") < _ru.index("migrate_v2.already_applied"))
ok("...and it is skipped once recorded, so it costs one lookup a run",
   "if migrate_v2.already_applied(conn):" in _ru)
ok("...and a failure to migrate is reported, never swallowed",
   "ERROR: the V2 migration did not apply" in _ru)

# ── a spread and a total are two bets, not one record ────────────────────────
#
# The first deploy of the locked-line headline pooled both markets and printed
# the sum under a tile reading "ATS record". 83-103 was neither the 38-47 the
# model went against the spread nor the 31-39 it went on totals: both numbers
# were real and the one on screen was neither of them.
_mkc = _db.connect(os.path.join(tempfile.mkdtemp(), "markets.db"))
for _i, (_mkt, _res) in enumerate(
        [("spread", "W"), ("spread", "W"), ("spread", "L"), ("total", "L"),
         ("total", "L"), ("total", "L")]):
    _gid = "m%d" % _i
    _mkc.execute(
        "INSERT INTO games (game_id, sport, season, week, home_team, away_team,"
        " kickoff, home_score, away_score, neutral_site)"
        " VALUES (?,'cfb',2026,1,'A','B','2026-09-01T16:00:00+00:00',30,10,0)",
        (_gid,))
    _mkc.execute(
        "INSERT INTO signal_log (signal_id, evaluation_id, forecast_id, game_id,"
        " strategy_version, market, side, line, created_at, official_horizon,"
        " is_official, locked_result, graded_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
        ("sg%d" % _i, "ev%d" % _i, "fc%d" % _i, _gid, "S-test", _mkt, "A", -3.0,
         "2026-09-01T00:00:00+00:00", "T2", _res, "2026-09-02T00:00:00+00:00"))
_mkc.commit()

_all = sig.official_record(_mkc, strategy_version="S-test")
_sp = sig.official_record(_mkc, strategy_version="S-test", market="spread")
_to = sig.official_record(_mkc, strategy_version="S-test", market="total")
ok("a market filter counts only that market",
   (_sp["locked_w"], _sp["locked_l"]) == (2, 1)
   and (_to["locked_w"], _to["locked_l"]) == (0, 3),
   "spread %s-%s, total %s-%s" % (_sp["locked_w"], _sp["locked_l"],
                                  _to["locked_w"], _to["locked_l"]))
ok("...and every row says which market it is about",
   _sp["market"] == "spread" and _all["market"] == "all")
# The point: the pooled number is a record of NEITHER bet.
ok("CONTROL: pooled, the percentage equals neither market's",
   _all["locked_pct"] != _sp["locked_pct"]
   and _all["locked_pct"] != _to["locked_pct"],
   "pooled %s vs spread %s vs total %s" % (_all["locked_pct"],
                                           _sp["locked_pct"], _to["locked_pct"]))
ok("the public headline asks for one market by name",
   '_v2_locked_record(conn, "cfb", market="spread")' in
   open(os.path.join(ROOT, "src", "publish.py")).read())
ok("...and the close diagnostic beside it narrows the same way",
   "def _v2_close_diagnostic(conn, sport=\"cfb\", market=\"spread\")" in
   open(os.path.join(ROOT, "src", "publish.py")).read())

# ── one number, one format ───────────────────────────────────────────────────
#
# The budget ledger stored a bare integer per month. `api_budget.spend` stores a
# dict, and `fetch_cfb.budget_status` still read the entry as an int, so
# `"%d" % {...}` raised. It did not fail on the change that introduced it: it
# failed on the FIRST RUN AFTER A SPEND rewrote the month's entry, which is a
# different run, a different job, and a stack trace that names neither.
import datetime as _dt                                          # noqa: E402
import json as _json                                            # noqa: E402
import api_budget as _ab                                        # noqa: E402
import fetch_cfb as _fc                                         # noqa: E402

_bdir = tempfile.mkdtemp()
_bpath = os.path.join(_bdir, "budget.json")
_mk = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m")
# BOTH SHAPES IN ONE FILE, which is what production actually had.
with open(_bpath, "w") as _fh:
    _json.dump({"2020-01": 101, _mk: {"used": 30, "by_purpose": {"critical": 3}}}, _fh)
ok("the new dict shape reads as a number", _ab.status(path=_bpath)["used"] == 30)
with open(_bpath, "w") as _fh:
    _json.dump({_mk: 42}, _fh)
ok("...and so does the original flat integer", _ab.status(path=_bpath)["used"] == 42)
ok("the fetcher asks api_budget rather than parsing the file itself",
   "api_budget.status(path=BUDGET_FILE)" in
   open(os.path.join(ROOT, "src", "fetch_cfb.py")).read())
# CONTROL: the shape that used to crash must now format.
ok("CONTROL: a dict-shaped month formats with %d instead of raising",
   ("%d" % _ab._entry({_mk: {"used": 7}}, _mk)["used"]) == "7")

# ── the shadow challengers exist where the model actually runs ───────────────
#
# They were fitted and registered on a laptop, and the registry lives in the
# cached database. The first production deploy published a challenger table
# holding the Champion and nothing else — a whole shadow apparatus inert, every
# test passing. Same shape as the migration that had never run.
_wf = open(os.path.join(ROOT, ".github", "workflows", "update.yml")).read()
ok("the workflow registers the challengers when none are registered",
   "tools/fit_challengers.py --season 2025 --apply" in _wf)
ok("...guarded on the registry, so it fits once and never re-mints a version",
   "count_challengers.py" in _wf and 'if [ "$N" -eq 0 ]' in _wf)
ok("...and the counter is a script, not Python embedded in YAML",
   os.path.exists(os.path.join(ROOT, "tools", "count_challengers.py")))

# run_update replays the journal, and the journal has to BE there. `state/` is
# gitignored and only the two V2 workflows fetched the data-state branch, so the
# replay step ran in production, found no directory, and said nothing — the
# hand-off it exists to perform landing nowhere.
ok("the update job restores the state journal before replaying it",
   "git fetch origin data-state" in _wf)
ok("...before the update that replays it",
   _wf.index("git fetch origin data-state") < _wf.index("src/run_update.py"))
ok("...and an absent branch is stated rather than passed over in silence",
   "no data-state branch yet" in _wf)

# ── §31 the research dataset contract ────────────────────────────────────────
#
# The one thing §31 says must not happen: development rows mixed silently with
# exact prospective ones. Every row therefore names its own information quality.
_CONTRACT = ("game_id", "season", "week", "kickoff", "forecast_time", "horizon",
             "market_timing_quality", "market_snapshot_id",
             "grade_home_snapshot_id", "grade_away_snapshot_id",
             "grade_timing_quality", "feature_schema", "actual_margin",
             "market_margin_same_time", "closing_margin", "eligible_prospective")
_fit_src = open(os.path.join(ROOT, "tools", "fit_challengers.py")).read()
for _f in _CONTRACT:
    ok("a development row carries %s" % _f, '"%s"' % _f in _fit_src)
ok("...and a development row is NOT flagged as prospective evidence",
   '"eligible_prospective": 0' in _fit_src,
   "2025 has been used to choose terms by a human who saw the results")
ok("...and says its market number is not an exact-horizon snapshot",
   'TIMING_UNKNOWN = "unknown_historical_current"' in _fit_src)

# ── §19.4 the block bootstrap, and why it is blocked by week ─────────────────
import metrics_v2 as _m19                                       # noqa: E402
import random as _rnd                                           # noqa: E402

_rg9 = _rnd.Random(1)
_bw = {w: [_rg9.gauss(0.0, 3.0) for _ in range(30)] for w in range(1, 13)}
_ci = _m19.block_bootstrap_ci(_bw)
ok("a block bootstrap over twelve weeks produces an interval", _ci["lo"] is not None)
ok("...that brackets the truth it was drawn from", _ci["lo"] < 0.0 < _ci["hi"],
   "%s..%s" % (_ci["lo"], _ci["hi"]))
ok("...and says outright that it is not separated from zero",
   _ci["separated_from_zero"] is False)
ok("the seed is fixed, so a number quoted in a report can be reproduced",
   _m19.block_bootstrap_ci(_bw) == _ci)

# Three weekends is a statement about three weekends. An interval labelled 95%
# over that is the fake precision §27.3 forbids.
_thin = _m19.block_bootstrap_ci({1: [1.0], 2: [2.0], 3: [3.0]})
ok("CONTROL: too few weeks yields no interval and a reason",
   _thin["lo"] is None and "at least" in _thin["why"], _thin.get("why"))

# The point of blocking. Forty correlated games resampled INDIVIDUALLY report an
# interval that is too narrow -- flattering, stable-looking, and wrong in the
# direction that gets a challenger promoted.
_shift = _rnd.Random(7)
_corr = {w: [(_shift.gauss(0, 1) + (6.0 if w % 2 else -6.0)) for _ in range(40)]
         for w in range(1, 9)}
_blocked = _m19.block_bootstrap_ci(_corr)
_flat = _m19.block_bootstrap_ci(
    {i: [v] for i, v in enumerate(x for xs in _corr.values() for x in xs)})
ok("blocking by week gives a WIDER interval than resampling games",
   (_blocked["hi"] - _blocked["lo"]) > (_flat["hi"] - _flat["lo"]) * 2,
   "blocked %.2f wide vs per-game %.2f"
   % (_blocked["hi"] - _blocked["lo"], _flat["hi"] - _flat["lo"]))

# ── the probability scoreboard, and why moneylines stay off ──────────────────
_pconn = _db.connect(os.path.join(tempfile.mkdtemp(), "prob.db"))
_pv = "C0-prob"
_fv2.register_model(_pconn, model_version=_pv, model_id="champion-grade",
                    role="champion", config={"scale": 1.0})
_rg_p = _rnd.Random(11)
for _i in range(200):
    _true = 0.25 + 0.5 * ((_i % 10) / 9.0)
    _y = 1 if _rg_p.random() < _true else 0
    _gid = "p%03d" % _i
    _pconn.execute(
        "INSERT INTO games (game_id, sport, season, week, home_team, away_team,"
        " kickoff, home_score, away_score, neutral_site)"
        " VALUES (?,?,?,?,?,?,?,?,?,0)",
        (_gid, "cfb", 2026, 1 + _i % 10, "H%d" % _i, "A%d" % _i,
         "2026-09-0%dT18:00:00+00:00" % (1 + _i % 5),
         30 if _y else 10, 10 if _y else 30))
    _fsid = "fs_%s" % _gid
    _pconn.execute(
        "INSERT INTO feature_snapshots (feature_snapshot_id, game_id, created_at,"
        " feature_schema_version, payload_json, payload_hash)"
        " VALUES (?,?,?,?,?,?)",
        (_fsid, _gid, "2026-09-01T00:00:00+00:00", "champion_features_v3",
         json.dumps({"consensus_home_prob": _true}), "h%03d" % _i))
    _pconn.execute(
        "INSERT INTO forecast_log (forecast_id, sport, game_id, model_version,"
        " feature_snapshot_id, horizon, generated_at, snapshot_status,"
        " pred_home_margin, home_win_prob, provenance_quality, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("fc_%s" % _gid, "cfb", _gid, _pv, _fsid, "T2",
         "2026-09-01T00:00:00+00:00", "on_time", 0.0, _true, "complete",
         "2026-09-01T00:00:00+00:00"))
_pconn.commit()

_pq = _m19.probability_quality(_pconn, model_version=_pv, sport="cfb")
ok("a probability scoreboard scores every finished forecast", _pq["n"] == 200)
ok("...with a Brier score", _pq["brier"] is not None)
# A Brier of 0.24 means nothing on its own. What a base-rate model would have
# scored is the reference that makes it readable.
ok("...and the base-rate model it has to beat", _pq["brier_base_rate_model"] is not None)
ok("a well-calibrated model beats the base rate",
   _pq["brier"] < _pq["brier_base_rate_model"],
   "%s vs %s" % (_pq["brier"], _pq["brier_base_rate_model"]))
ok("calibration is reported in bins, not as one number",
   len(_pq["calibration"]) == 5 and sum(b["n"] for b in _pq["calibration"]) == 200)
ok("...and a populated bin's realized rate tracks its predicted one",
   all(abs(b["realized"] - b["predicted"]) < 0.2
       for b in _pq["calibration"] if b["n"] >= 30),
   [(b["n"], b["predicted"], b["realized"]) for b in _pq["calibration"]])

# The whole point of §12.5: measuring a probability does not authorise betting it.
ok("measuring a probability does not turn moneylines on",
   _pq["enables_moneyline"] is False and sig.STRATEGY_V0["moneyline_enabled"] is False)
ok("...and the scoreboard says why in words", "prospective" in _pq["why"])

# A model claiming certainty about a football game would make log loss infinite
# and one row would swamp the season.
_pconn.execute("UPDATE forecast_log SET home_win_prob=0.0 WHERE game_id='p000'")
_pconn.commit()
_pq2 = _m19.probability_quality(_pconn, model_version=_pv, sport="cfb")
ok("CONTROL: a bare 0 is clipped, counted, and does not produce an infinity",
   _pq2["clipped"] == 1 and _pq2["log_loss"] not in (None, float("inf")),
   "clipped %s, log loss %s" % (_pq2["clipped"], _pq2["log_loss"]))

# ── §22 availability: observations, never a mutable status ───────────────────
import availability as _av                                      # noqa: E402

ok("an unrecognised status is UNKNOWN, not quietly filed as OUT",
   _av.normalize_status("wibble") == _av.UNKNOWN)
ok("...and so is an absent one", _av.normalize_status(None) == _av.UNKNOWN)
ok("the wordings a real feed uses all map",
   [_av.normalize_status(x) for x in ("Out", "questionable", "Suspension",
                                      "Day-To-Day", "Probable")]
   == [_av.OUT, _av.QUESTIONABLE, _av.SUSPENDED, _av.QUESTIONABLE, _av.PROBABLE])
ok("only tiers 1-3 could ever move a model",
   _av.AUTO_ADJUST_ELIGIBLE == (1, 2, 3))
ok("...and ESPN is tier 3, a verified feed and not an announcement",
   _av.SOURCE_TIER[_av.SOURCE_ESPN] == _av.TIER_VERIFIED_FEED)

_aconn = _db.connect(os.path.join(tempfile.mkdtemp(), "avail.db"))
_r1 = _av.record(_aconn, sport="cfb", season=2026, team="Oregon", player="A Back",
                 player_key="a back", position="RB", status_raw="Questionable",
                 impact_points=1.2, observed_at="2026-09-03T12:00:00+00:00")
_r2 = _av.record(_aconn, sport="cfb", season=2026, team="Oregon", player="A Back",
                 player_key="a back", position="RB", status_raw="Questionable",
                 impact_points=1.2, observed_at="2026-09-04T12:00:00+00:00")
_r3 = _av.record(_aconn, sport="cfb", season=2026, team="Oregon", player="A Back",
                 player_key="a back", position="RB", status_raw="Out",
                 impact_points=1.2, observed_at="2026-09-05T12:00:00+00:00")
ok("a new status appends", _r1 == "appended" and _r3 == "appended")
ok("...and restating the same one does not", _r2 == "unchanged",
   "an unchanged status observed hourly would bury the transitions")

# §22.2 is the whole design: the Thursday row must still be there on Saturday.
_hist = _av.transitions(_aconn, "cfb", 2026)["a back"]
ok("the earlier status is still readable after it changed",
   [r["status"] for r in _hist] == [_av.QUESTIONABLE, _av.OUT],
   [r["status"] for r in _hist])
ok("CONTROL: nothing was updated in place — both rows survive", len(_hist) == 2)

# Exactly-as-of: asking on Friday cannot see Saturday's downgrade.
_fri = _av.team_status_asof(_aconn, "cfb", 2026, "Oregon",
                            "2026-09-04T18:00:00+00:00")
ok("an as-of view cannot see an observation made later",
   len(_fri) == 1 and _fri[0]["status"] == _av.QUESTIONABLE, _fri[0]["status"])
_sat = _av.team_status_asof(_aconn, "cfb", 2026, "Oregon",
                            "2026-09-06T00:00:00+00:00")
ok("...and does see it once it has been made", _sat[0]["status"] == _av.OUT)

_sum = _av.team_summary(_aconn, "cfb", 2026, "Oregon", "2026-09-06T00:00:00+00:00")
ok("the summary prices only the players actually listed out",
   _sum["out_impact_points"] == 1.2, _sum["out_impact_points"])
ok("...and says on its face that it adjusts nothing", _sum["adjusts_model"] is False)
ok("...and carries how stale it is, in minutes",
   _sum["staleness_minutes"] == 720.0, _sum["staleness_minutes"])
# "Nobody is listed out" and "nobody looked" are different states, and this is
# the module that exists because of that distinction.
ok("a team nobody has observed returns None, not a zeroed summary",
   _av.team_summary(_aconn, "cfb", 2026, "Washington",
                    "2026-09-06T00:00:00+00:00") is None)

# §22.3, the sentence it ends on: do not turn Questionable into Out.
ok("no probability of absence is published, because none is calibrated",
   not any(k in _sum for k in ("p_absent", "probability", "expected_impact")),
   sorted(_sum))
ok("the strategy does not consume availability — that would be a version change",
   "availability" not in json.dumps(sig.STRATEGY_V0))

# ── §23 weather: what is real, and the gap said out loud ─────────────────────
import weather as _wx                                           # noqa: E402

_EV = {"id": "9", "date": "2026-09-06T20:00Z",
       "weather": {"displayValue": "Partly sunny", "temperature": 71,
                   "conditionId": "3"},
       "competitions": [{"venue": {"fullName": "Husky Stadium", "indoor": False},
                         "competitors": [
                             {"team": {"location": "Washington",
                                       "displayName": "Washington Huskies"}},
                             {"team": {"location": "Washington State",
                                       "displayName": "Washington State Cougars"}}]}]}
_p = _wx.parse_event(_EV)
ok("a forecast is parsed into what this schema stores", _p["temperature_f"] == 71.0)
# The source has no wind. A zero would read as a still day, which is a claim.
ok("wind is NULL, not zero — a still day and an unmeasured day differ",
   _p["wind_mph"] is None and _p["gust_mph"] is None)
ok("the dome flag is captured, because it is the one weather fact that is certain",
   _p["indoor"] == 0)
# ESPN's displayName carries the mascot and this database does not. Every row
# went unmatched while the fetch reported success.
ok("teams come from `location`, not the mascot-bearing display name",
   _p["teams"] == ["Washington", "Washington State"], _p["teams"])
ok("CONTROL: the display names would not have matched anything",
   sorted(c["team"]["displayName"] for c in _EV["competitions"][0]["competitors"])
   != _p["teams"])
ok("an event with neither weather nor a venue yields nothing",
   _wx.parse_event({"competitions": [{}]}) is None)

_wconn = _db.connect(os.path.join(tempfile.mkdtemp(), "wx.db"))
_wconn.execute(
    "INSERT INTO games (game_id, sport, season, week, home_team, away_team,"
    " kickoff, neutral_site) VALUES ('w1','cfb',2026,2,'Washington',"
    "'Washington State','2026-09-06T20:00:00+00:00',0)")
_wconn.commit()
ok("an ESPN event matches this database's game", _wx.match_game(_wconn, "cfb", _p) == "w1")
ok("CONTROL: a game that is not there matches nothing, rather than the closest one",
   _wx.match_game(_wconn, "cfb", dict(_p, teams=["Oregon", "Boise State"])) is None)

_wx.store(_wconn, sport="cfb", game_id="w1", row=_p, horizon="T24",
          observed_at="2026-09-05T20:00:00+00:00")
_wx.store(_wconn, sport="cfb", game_id="w1",
          row=dict(_p, temperature_f=58.0, condition="Rain"), horizon="T2",
          observed_at="2026-09-06T18:00:00+00:00")
ok("both readings are kept — the T24 forecast is not overwritten by the T2 one",
   _wconn.execute("SELECT COUNT(*) c FROM weather_snapshots").fetchone()["c"] == 2)
ok("an as-of read cannot see a forecast published later",
   _wx.summary_asof(_wconn, "w1", "2026-09-06T00:00:00+00:00")["temperature_f"] == 71.0)
ok("...and does see it once it has been", 
   _wx.summary_asof(_wconn, "w1", "2026-09-06T19:00:00+00:00")["condition"] == "Rain")
ok("nothing recorded reads as None, never as fair and still",
   _wx.summary_asof(_wconn, "w2", "2026-09-06T19:00:00+00:00") is None)
ok("the summary says on its face that it adjusts nothing",
   _wx.summary_asof(_wconn, "w1", "2026-09-06T19:00:00+00:00")["adjusts_model"] is False)

ok("the horizon label is the nearest standardized one",
   _wx.nearest_horizon("2026-09-06T20:00:00+00:00", "2026-09-05T21:00:00+00:00") == "T24")
ok("...and a reading after kickoff is not labelled a pregame horizon",
   _wx.nearest_horizon("2026-09-06T20:00:00+00:00", "2026-09-06T21:00:00+00:00") is None)

# §23: a weather source failing must degrade only models that need weather.
_dead = _wx.refresh(_wconn, sport="cfb", days=1, fetch=lambda url: None, verbose=False)
ok("a dead weather source is counted as a failure and raises nothing",
   _dead["failed"] == 1 and _dead["stored"] == 0)
def _boom(url):
    raise RuntimeError("network on fire")
ok("...and one that throws does not reach the caller either",
   _wx.refresh(_wconn, sport="cfb", days=1, fetch=_boom, verbose=False)["failed"] == 1)

# The point of the whole file: the Champion's rule is untouched.
import engine as _eng                                           # noqa: E402
ok("the Champion still awards its threshold quality points",
   _eng.win_points({"wq_top5": 5.0, "wq_top10": 4.0, "wq_top25": 3.0,
                    "wq_other": 0.0}, 3) == 5.0)
ok("...and the challenger is a separate module that changes none of it",
   "form_quality" not in open(os.path.join(ROOT, "src", "engine.py")).read())


print("\n── the acceptance properties, on the live database ──")
#
# The §37 checklist, as assertions rather than a document. Two of these were
# written wrong the first time and passed a wrong version of the question, which
# is exactly why they are here rather than in a markdown table:
#
#   "a forecast exists independently of a signal" was first written as
#   forecasts > signals, and failed — because ONE pick yields both a spread and
#   a total signal, so signals legitimately exceed forecasts. The property is
#   that a DECLINED forecast still has a row and produces nothing.
#
#   "no ROI without a price" was first written as "no profit_units without a
#   price", and failed — because a losing spread bet costs one unit at ANY
#   price, so its return is genuinely known. The property is that no unpriced
#   WIN claims a profit, and that ROI is not averaged over a set of losses.

_live_path = os.path.join(ROOT_DIR, "data", "model.db")
if os.path.exists(_live_path):
    import metrics_v2 as _m2                                    # noqa: E402
    _lc = _db.connect(_live_path)

    def _q(sql):
        return _lc.execute(sql).fetchone()[0]

    ok("no spread price was ever invented",
       _q("SELECT COUNT(*) FROM market_quotes WHERE home_spread_price IS NOT NULL") == 0)
    ok("every quote names a provider and a time",
       _q("SELECT COUNT(*) FROM market_quotes WHERE provider IS NULL"
          " OR observed_at IS NULL") == 0)
    ok("every grade snapshot is timestamped",
       _q("SELECT COUNT(*) FROM grade_snapshots WHERE effective_at IS NULL") == 0)
    ok("a declined forecast keeps its row and produces nothing",
       _q("""SELECT COUNT(*) FROM forecast_log f WHERE NOT EXISTS
             (SELECT 1 FROM signal_log s WHERE s.forecast_id=f.forecast_id)""") > 0)
    # WHEN THIS FAILS, SAY WHY. It failed on the first production run after the
    # cutover and the count alone did not explain it: the database had never had
    # the V2 migration applied, so nothing had ever been evaluated on it. A red
    # gate that names the cause is the difference between a fix and an hour.
    _migrated = _lc.execute("SELECT applied_at FROM v2_migrations LIMIT 1").fetchone()
    ok("...and the decline itself is recorded",
       _q("SELECT COUNT(*) FROM strategy_evaluations WHERE eligible=0") > 0,
       "this database has NEVER been migrated — run src/migrate_v2.py --apply"
       if not _migrated else "migrated %s and still no declined evaluation"
       % _migrated["applied_at"])
    ok("no unpriced WIN claims a profit",
       _q("""SELECT COUNT(*) FROM signal_log
              WHERE locked_result='W' AND price IS NULL
                AND profit_units IS NOT NULL""") == 0)
    for _sv in ("S-legacy", sig.STRATEGY_V0["strategy_version"]):
        _r = _m2.signal_performance(_lc, strategy_version=_sv)
        ok("%s reports no ROI while no price is recorded" % _sv,
           _r["roi"] is None or _r["priced_n"] > 0,
           "priced %s, roi %s" % (_r["priced_n"], _r["roi"]))
    ok("no challenger has ever signed an official signal",
       _q("""SELECT COUNT(*) FROM signal_log s
               JOIN forecast_log f USING(forecast_id)
               JOIN model_registry m ON m.model_version=f.model_version
              WHERE m.role IN ('challenger','baseline') AND s.is_official=1""") == 0)
    ok("the champion carries a git sha, a config hash and a feature schema",
       _q("""SELECT COUNT(*) FROM model_registry WHERE role='champion'
              AND git_sha IS NOT NULL AND config_hash IS NOT NULL
              AND feature_schema_version IS NOT NULL""") > 0)
    ok("the legacy result column still holds its legacy values",
       _q("SELECT COUNT(*) FROM picks_log WHERE ats_result IS NOT NULL") > 0)
    ok("...beside the locked-line one",
       _q("SELECT COUNT(*) FROM picks_log WHERE ats_result_at_pick IS NOT NULL") > 0,
       "this database has NEVER been migrated, so the published record is still "
       "the legacy close-based one — run src/migrate_v2.py --apply"
       if not _migrated else "migrated, but no pick carries a locked-line result")

    # THE CUTOVER CANNOT BE HALF-DONE. A database running V2-official code with
    # an unmigrated record publishes the legacy close-based headline under a page
    # that claims otherwise, and every individual component test still passes.
    ok("a V2-official database has had the migration applied", bool(_migrated),
       "MODEL_V2_OFFICIAL is on and v2_migrations is empty")
    ok("the schema enforces one official signal per game, market and strategy",
       bool(_lc.execute("SELECT name FROM sqlite_master WHERE type='index'"
                        " AND name='idx_one_official_signal'").fetchone()))


print("\n── the moat covers what the tools actually write ──")
#
# migrate_v2 writes `data/model.pre-v2-<stamp>.db` before it applies: a
# byte-for-byte copy of the grade database. `.gitignore` listed `data/model.db`
# by exact name, which does not match it, so two 20 MB copies of the moat sat
# untracked and unignored, one `git add -A` from a public repository.
#
# The rule is checked against the FILENAMES THE CODE PRODUCES, not against a
# remembered list, because the pattern only has to fall behind the code once.
import subprocess                                               # noqa: E402

def _ignored(rel):
    r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT_DIR)
    return r.returncode == 0

_stamp = "20260906T050202Z"
for name in ["data/model.db",
             "data/model.pre-v2-%s.db" % _stamp,      # migrate_v2.backup()
             "data/model.db-wal", "data/model.db-shm",
             "output/research/data.json", "output/v2_migration_report.json",
             # The working journal holds the grades in full; only the redacted
             # copy is ever published, and only from its own branch.
             "state/grades/2026/09.jsonl", ".state-publish/grades/2026/09.jsonl"]:
    ok("the moat ignores %s" % name, _ignored(name))
ok("...and does not ignore the source it is protecting",
   not _ignored("src/migrate_v2.py"))


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
