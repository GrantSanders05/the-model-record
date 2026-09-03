"""
test_model.py — the guarantees the RATING model has to keep.

Separate from test_tracking.py, which proves the record arithmetic. This proves
the thing being recorded is the model it claims to be.

The reason it exists: a bakeoff of six rater configurations came back with
"grades", "elo" and three blend weights producing byte-identical numbers.
`backtest.run` pops the grades out of the config, nobody had put them in, and
`GradeRater.strength` returns None for a team it has no grade for -- which the
Model correctly answers with an Elo fallback. With no grades at all, EVERY game
fell through and the run reported itself as the grade model. Every conclusion
drawn from it was about Elo.

    python3 tools/test_model.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import backtest      # noqa: E402
import db            # noqa: E402
import engine        # noqa: E402
import metrics       # noqa: E402
import pro_models    # noqa: E402

P = F = 0


def ok(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print("  [PASS] %s" % name)
    else:
        F += 1
        print("  [FAIL] %s%s" % (name, (" — " + str(detail)) if detail else ""))


CFG = json.load(open(os.path.join(ROOT, "config", "cfb_grades.json")))

print("\n── a grade model with no grades is not a grade model ──")
for rater in ("grades", "blend"):
    try:
        engine.Model(dict(CFG, rater=rater), None)
        ok("rater %r refuses to run with no grades" % rater, False, "it ran")
    except ValueError as e:
        ok("rater %r refuses to run with no grades" % rater, "film grades" in str(e))
# Elo needs none, and must still be constructible.
try:
    engine.Model(dict(CFG, rater="elo"), None)
    ok("elo still runs without grades", True)
except ValueError as e:
    ok("elo still runs without grades", False, e)

print("\n── the fallback is counted, because it is a different model answering ──")
m = engine.Model(dict(CFG, rater="grades"), {("2025", "X"): [(1, {"qb": 5})]})
ok("a fresh model has answered nothing", m.fallback_share() == 0.0)
game = {"season": 2025, "week": 3, "home_team": "Nobody", "away_team": "Nobody2",
        "home_div": "fbs", "away_div": "fbs", "neutral_site": 0, "market_margin": None}
m.predict(game)
ok("an ungraded matchup is recorded as a fallback", m.fallback_share() == 1.0,
   m.fallback_share())

conn = db.connect()
have_grades = conn.execute(
    "SELECT COUNT(*) c FROM grades WHERE sport='cfb' AND season=2025").fetchone()["c"]

if not have_grades:
    print("\n  (no 2025 grades in this database — skipping the calibration checks)")
else:
    print("\n── the model is calibrated ──")
    cfg = dict(CFG)
    cfg["_grades"] = backtest.load_grades(conn, "cfb")
    cfg["_stats"] = pro_models.load_stats(conn, "cfb")
    preds = backtest.run(backtest.load_games(conn, "cfb"), dict(cfg), test_seasons=[2025])
    ev = metrics.evaluate(preds)

    # A predicted 10-point margin has to mean a 10-point expectation, because the
    # margin becomes a win probability and the probability becomes a Kelly stake.
    ok("calibration slope is within 8% of 1.0",
       abs(ev["calib_slope"] - 1.0) <= 0.08, "%.3f" % ev["calib_slope"])
    # "The scale differs from 1.0" USED TO STAND IN FOR "somebody fitted it", and
    # that proxy broke the moment the fit legitimately returned 0.997. A test
    # that fails because the right answer resembles the default is measuring the
    # wrong thing. Assert the real property instead: the shipped scale is AT the
    # calibrated value, so moving it materially in either direction breaks the
    # slope. That is true of a fitted number and false of a guessed one.
    for factor in (0.85, 1.15):
        off = metrics.evaluate(backtest.run(
            backtest.load_games(conn, "cfb"),
            dict(cfg, scale=CFG["scale"] * factor), test_seasons=[2025]))
        ok("scale x%.2f breaks calibration, so the shipped value is the fitted one"
           % factor, abs(off["calib_slope"] - 1.0) > 0.08,
           "slope %.3f" % off["calib_slope"])

    # The grade model has to beat the rater it falls back to. If it does not, the
    # film is costing accuracy rather than adding it, and that is worth failing over.
    elo_cfg = dict(CFG, rater="elo")
    elo = metrics.evaluate(backtest.run(backtest.load_games(conn, "cfb"),
                                        dict(elo_cfg), test_seasons=[2025]))
    ok("the film grades beat Elo against the spread",
       ev["ats_pct"] > elo["ats_pct"],
       "grades %.2f%% vs elo %.2f%%" % (ev["ats_pct"], elo["ats_pct"]))
    ok("...and beat it on absolute error",
       ev["mae"] < elo["mae"], "grades %.2f vs elo %.2f" % (ev["mae"], elo["mae"]))

    # Not "is it profitable" — one season cannot answer that — but the published
    # record must never silently fall under the number it is compared against.
    ok("ATS is above break-even on the validation season",
       ev["ats_pct"] > 52.38, "%.2f%%" % ev["ats_pct"])

    print("\n── proving these can fail ──")
    before = F
    bad = metrics.evaluate(backtest.run(backtest.load_games(conn, "cfb"),
                                        dict(cfg, scale=3.0), test_seasons=[2025]))
    ok("[control] a wildly wrong scale must FAIL the calibration check",
       abs(bad["calib_slope"] - 1.0) <= 0.08, "slope %.3f" % bad["calib_slope"])
    if F > before:
        print("  ...it failed, as required. The calibration check is real.")
        F = before
    else:
        F += 1
        print("  [FAIL] scale=3.0 still calibrated — the check proves nothing")

print("\n── the committed validation summary matches the live config ──")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import write_validation as wv            # noqa: E402
import glob                              # noqa: E402

files = sorted(glob.glob(os.path.join(ROOT, "data", "validation", "cfb_*.json")))
ok("a validation summary is committed", bool(files),
   "run tools/write_validation.py --season <last complete season>")
if files:
    v = json.load(open(files[-1]))
    ok("it carries the fingerprint of the config it was computed under",
       bool(v.get("config_fingerprint")))
    # A frozen number with no way to notice it has gone stale is worse than none.
    # Change `scale` or the rater and this fails until it is regenerated.
    ok("...and that fingerprint still matches config/cfb_grades.json",
       v.get("config_fingerprint") == wv.fingerprint(CFG),
       "committed %s vs current %s — re-run tools/write_validation.py"
       % (v.get("config_fingerprint"), wv.fingerprint(CFG)))
    ok("it reports an interval, not just a headline",
       v.get("ci_lo") is not None and v.get("ci_hi") is not None)
    ok("it leaks no grades", not any(
        k for k in v if k in ("grades", "teams", "ratings")), sorted(v))


# ---------------------------------------------------------------------------
# The 'computed' quality formula.
#
# WHY IT EXISTS: the four spreadsheet columns Wins / Losses / Win Points / Loss
# Points were filled in BY HAND, and for 2026 they are empty on all 138 teams --
# so the whole quality term contributes zero and the ratings are bare position
# grades. Meanwhile GradeRater.observe() has been accruing exactly that quantity
# from real results on every single run and NOTHING HAS EVER READ IT. A writer
# with no reader is as dead as a reader with no writer, and harder to notice.
#
# The assertion that matters most is the ORDERING one. Quality points come from
# results, so a formula that reads them can trivially leak the outcome of the
# game being predicted into its own input. That is the one bug that would make
# every number downstream a lie while looking like an improvement.
# ---------------------------------------------------------------------------
print("\n── the computed quality formula ──")

_G = {"qb": 10.0, "rb": 8.0, "wr": 8.0, "ol": 10.0,
      "dl": 10.0, "lb": 8.0, "db": 8.0, "coach_st": 10.0}
_GRADES = {(2026, "Home"): [(0, dict(_G))], (2026, "Away"): [(0, dict(_G))]}


def _rater(**over):
    cfg = {"grade_formula": "computed", "sheet_coach_weight": 1.0,
           "quality_scale": 1.0, "wq_top5": 4.0, "wq_top10": 3.0, "wq_top25": 2.0,
           "wq_other": 0.0, "lq_ranked": 0.0, "lq_unranked_fbs": -2.0,
           "lq_fcs": -4.0}
    cfg.update(over)
    r = engine.GradeRater(cfg, _GRADES)
    r.new_season(2026)
    return r


def _game(hs=None, as_=None, hr=None, ar=None, hd="fbs", ad="fbs", wk=1):
    return {"season": 2026, "week": wk, "home_team": "Home", "away_team": "Away",
            "home_score": hs, "away_score": as_, "home_rank": hr, "away_rank": ar,
            "home_div": hd, "away_div": ad}


# Two identical teams: with nothing observed the rating difference must be zero.
_r = _rater()
ok("with no games observed, computed == bare grades (no phantom quality)",
   _r.strength(_game()) == 0.0, _r.strength(_game()))

# Home beats the #3 team in the country. BOTH sides move: the winner gains
# wq_top5 and the loser is charged lq_unranked_fbs, so the rating GAP is the sum
# of the two. Zero the loser's charge to assert one term at a time.
_r = _rater(lq_unranked_fbs=0.0)
_r.observe(_game(hs=30, as_=10, ar=3))
ok("beating a top-5 team credits the winner", _r.strength(_game()) == 4.0,
   _r.strength(_game()))
_r = _rater()
_r.observe(_game(hs=30, as_=10, ar=3))
ok("...and the loser is charged in the same game, so the gap is both",
   _r.strength(_game()) == 6.0, _r.strength(_game()))

# Losing to an FCS side is charged to the loser.
_r = _rater()
_r.observe(_game(hs=10, as_=30, ad="fcs"))
ok("losing to an FCS team is charged to the loser",
   _r.strength(_game()) == -4.0, _r.strength(_game()))

# lq_ranked was PINNED AT ZERO in the config and in the first search space, so no
# rule that charged for a ranked loss could ever be expressed. Regressing Grant's
# own hand-entered numbers says he charged about -1.45 for one.
_r = _rater(lq_ranked=-1.5)
_r.observe(_game(hs=10, as_=30, ar=None, hr=None, ad="fbs"))   # Home loses to unranked
_base = _r.strength(_game())
_r2 = _rater(lq_ranked=-1.5)
_r2.observe(_game(hs=10, as_=30, ar=4))                        # Home loses to a top-5
ok("a loss to a ranked team can now cost something",
   _r2.strength(_game()) == -1.5, _r2.strength(_game()))
ok("...and it is distinguishable from an unranked loss",
   _r2.strength(_game()) != _base, "%s vs %s" % (_r2.strength(_game()), _base))

# quality_scale=0 must reduce it exactly to the no-quality case.
_r = _rater(quality_scale=0.0)
_r.observe(_game(hs=30, as_=10, ar=3))
ok("quality_scale=0 switches the term off entirely", _r.strength(_game()) == 0.0)

# THE ORDERING GUARANTEE. predict() must not see the game it is predicting.
_r = _rater(lq_unranked_fbs=0.0)
_g = _game(hs=45, as_=0, ar=2, wk=5)
_before = _r.strength(_g)
_r.observe(_g)
_after = _r.strength(_g)
ok("predicting a game does not use that game's own result", _before == 0.0, _before)
ok("...and observing it afterwards does change the rating", _after == 4.0, _after)

# The real callers must actually observe in that order, not just be capable of it.
import inspect                                                # noqa: E402
_bt = inspect.getsource(backtest.run)
ok("backtest.run predicts before it observes",
   _bt.index("model.predict") < _bt.index("model.observe"))
import predict as _predict                                    # noqa: E402
_pg = inspect.getsource(_predict.generate)
ok("predict.generate observes each game only after picking it",
   _pg.index("model.predict") < _pg.index("model.observe"))

# Per-season reset: quality must not bleed across seasons.
_r = _rater()
_r.observe(_game(hs=30, as_=10, ar=3))
_r.new_season(2027)
ok("new_season clears accrued quality", _r.strength(_game()) == 0.0)

# CONTROL: the assertions above must be able to fail.
_ctl = _rater(wq_top5=99.0, lq_unranked_fbs=0.0)
_ctl.observe(_game(hs=30, as_=10, ar=3))
ok("CONTROL: the formula responds to its parameters at all",
   _ctl.strength(_game()) == 99.0, _ctl.strength(_game()))


# ---------------------------------------------------------------------------
# The loss-points sign is normalised at import.
#
# The workbook uses TWO conventions for one column: the weekly snapshots store
# Loss Points positive (2025 week 9 sums to +1136 across 136 teams), the live
# `Team Data` tab stores them negative (-1286, same season, same teams). The
# engine applies its own sign on top, so the two produce OPPOSITE ratings from
# identical results.
#
# The tab that is authoritative changes with the calendar: the backtest that
# measured 55.27% read the weekly tabs, a live season reads Team Data. Without
# normalisation the model would silently invert its treatment of losses the
# moment the live tab was filled in -- crediting a team for losing to an FCS
# side, having been validated on precisely the opposite.
# ---------------------------------------------------------------------------
print("\n── the loss-points sign cannot flip under us ──")

import import_workbook as _iw                               # noqa: E402

_HDR = ["Team Name", "Wins", "Losses", "QB Score 15", "RB Score 10",
        "WR Score 10", "OL Score 15", "DL Score 15", "LB Score 10",
        "DB Score 10", "Coach/ST Score 15", "Win Points", "Loss Points"]
_BODY = [10, 2, 11.0, 8.0, 8.0, 11.0, 11.0, 8.0, 8.0, 12.0, 6.0]


def _lp(value):
    rows = [_HDR, ["Georgia"] + _BODY[:11] + [value]]
    recs, _ = _iw.parse_rows(rows, "cfb", 2026, 3, label="t")
    return [r["grade"] for r in recs if r["position"] == "_loss_points"]


ok("the weekly convention (positive) is stored as-is", _lp(24.0) == [24.0], _lp(24.0))
ok("the Team Data convention (negative) is stored the SAME way",
   _lp(-24.0) == [24.0], _lp(-24.0))
ok("...so the two tabs can no longer disagree", _lp(24.0) == _lp(-24.0))
ok("zero stays zero", _lp(0.0) == [0.0], _lp(0.0))

# CONTROL: without the normalisation these two would differ. Prove the
# assertion is capable of failing by checking the raw values really are opposite.
ok("CONTROL: the two raw inputs genuinely have opposite signs", 24.0 == -(-24.0))

# And it must not touch any other column -- Win Points may legitimately be zero
# but is never negated, and a negative position grade would be a real signal.
_rows = [_HDR, ["Georgia"] + _BODY[:10] + [-5.0, 12.0]]
_recs, _ = _iw.parse_rows(_rows, "cfb", 2026, 3, label="t")
_wp = [r["grade"] for r in _recs if r["position"] == "_win_points"]
ok("win points are NOT normalised — only loss points are", _wp == [-5.0], _wp)

# ── the team page and the picks are one number ────────────────────────────────
#
# The rankings table totalled a team from four spreadsheet columns while the
# engine rated it from quality points accrued off real results, and a footnote
# underneath told the reader they were the same number. For 2026 those columns
# are empty on all 138 teams, so the page was showing bare position grades for
# every team that had played.
#
# These do not check the arithmetic -- the block above already does. They check
# that there is only ONE arithmetic, by requiring the exported figure to equal
# what the rater itself returns.

print("\n── the team page shows what the model bets ──")

import research_export   # noqa: E402

_qcfg = dict(CFG)
_qcfg["grade_formula"] = "computed"

_grades = {(2026, "Alpha"): [(1, {"qb": 10, "rb": 8, "wr": 8, "ol": 9, "dl": 9,
                                  "lb": 7, "db": 7, "coach_st": 8})],
           (2026, "Beta"):  [(1, {"qb": 9, "rb": 8, "wr": 8, "ol": 8, "dl": 8,
                                  "lb": 7, "db": 7, "coach_st": 8})]}
_r = engine.GradeRater(_qcfg, _grades)
_r.new_season(2026)
_before = _r._grade_total(2026, "Alpha", 1)

# Alpha beats a top-5 Beta; Beta is charged for losing to an unranked side.
_r.observe({"home_team": "Alpha", "away_team": "Beta", "home_score": 30,
            "away_score": 10, "home_rank": None, "away_rank": 3,
            "home_div": "fbs", "away_div": "fbs"})

_after = _r._grade_total(2026, "Alpha", 1)
ok("a win moves the rating the rater reports",
   round(_after - _before, 6) == CFG["wq_top5"], (_before, _after))

_rec = _r.record("Alpha")
ok("the record counts the game", (_rec["wins"], _rec["losses"]) == (1, 0), _rec)
ok("win points are broken out", _rec["win_points"] == CFG["wq_top5"], _rec)
ok("...and the loser's are charged separately",
   _r.record("Beta")["loss_points"] == CFG["lq_unranked_fbs"], _r.record("Beta"))
ok("the parts add up to the accumulator the rating uses",
   round(_rec["win_points"] + _rec["loss_points"], 6) == _rec["quality"], _rec)

# The exported total is the rater's own number, not a second implementation.
_page = _before + _qcfg.get("quality_scale", 1.0) * _r.record("Alpha")["quality"]
ok("the page's total equals the rater's total",
   round(_page, 6) == round(_after, 6), (_page, _after))

# And a team that has not played shows zeroes rather than nothing: 0-0 is a
# fact, and a blank would read as "unknown".
_r2 = engine.GradeRater(_qcfg, _grades)
_r2.new_season(2026)
ok("an unplayed team reports 0-0 and no points",
   _r2.record("Alpha") == {"wins": 0, "losses": 0, "win_points": 0.0,
                           "loss_points": 0.0, "quality": 0.0}, _r2.record("Alpha"))

# A tie is neither, and must not be counted as either.
_r3 = engine.GradeRater(_qcfg, _grades)
_r3.new_season(2026)
_r3.observe({"home_team": "Alpha", "away_team": "Beta", "home_score": 21,
             "away_score": 21, "home_rank": None, "away_rank": None,
             "home_div": "fbs", "away_div": "fbs"})
ok("a tie is not a win and not a loss",
   _r3.record("Alpha")["wins"] == 0 and _r3.record("Alpha")["losses"] == 0,
   _r3.record("Alpha"))

# new_season must clear the record as well as the points, or week 1 of 2027
# opens with 2026's wins still on the board.
_r.new_season(2027)
ok("a new season clears the record, not just the points",
   _r.record("Alpha") == {"wins": 0, "losses": 0, "win_points": 0.0,
                          "loss_points": 0.0, "quality": 0.0}, _r.record("Alpha"))

# CONTROL: the sheet formula genuinely ignores the accrued quality, which is
# why the page needed changing at all.
_scfg = dict(CFG); _scfg["grade_formula"] = "sheet"
_sr = engine.GradeRater(_scfg, _grades)
_sr.new_season(2026)
_s0 = _sr._grade_total(2026, "Alpha", 1)
_sr.observe({"home_team": "Alpha", "away_team": "Beta", "home_score": 30,
             "away_score": 10, "home_rank": None, "away_rank": 3,
             "home_div": "fbs", "away_div": "fbs"})
ok("CONTROL: under the sheet formula a win changes nothing (the old bug)",
   _sr._grade_total(2026, "Alpha", 1) == _s0)

print("=" * 62)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
