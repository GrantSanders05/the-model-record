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
    ok("the config's scale is the fitted one, not a default",
       abs(CFG["scale"] - 1.0) > 0.01, CFG["scale"])

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

print("=" * 62)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
