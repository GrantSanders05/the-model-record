"""
validate_harness.py — prove the evaluation harness can tell good from bad.

A backtester that reports ~50% ATS for everything is indistinguishable from a
backtester that is simply broken. Before believing "Elo scores 50.2%, therefore
the market is efficient", the harness has to demonstrate that it WOULD have
reported a big number if a big edge were really there.

Five probes, each with an expected outcome. A probe that passes for the wrong
reason is worse than no probe, so each one asserts a specific range, not just
"looks fine".

    1 ORACLE          predicts the true final margin exactly    -> ~100% ATS
    2 ANTI-ORACLE     deliberately picks the losing side        -> ~0% ATS
    3 MARKET COPY     predicts exactly the closing line         -> no bets placed
    4 MARKET + NUDGE  closing line + 25% of the true residual   -> clearly > 52.38%
    5 COIN FLIP       random predictions                        -> ~50%, NOT significant

Probes 1 and 2 prove the sign convention and grading logic are right way round.
Probe 4 is the important one: it proves a genuine but MODEST edge is detected
and flagged significant. Probe 5 proves noise is not.

Run:  python3 src/validate_harness.py
"""

import random
import sys

import backtest
import db
import metrics

FAILURES = []


def check(name, condition, detail):
    status = "PASS" if condition else "FAIL"
    print("  [%s] %-16s %s" % (status, name, detail))
    if not condition:
        FAILURES.append(name)


def build(games, fn):
    """Turn games into predictions using fn(game) -> predicted home margin."""
    out = []
    for g in games:
        if g["home_score"] is None or g["market_margin"] is None:
            continue
        actual = g["home_score"] - g["away_score"]
        out.append({
            "pred_margin": fn(g, actual),
            "market_margin": g["market_margin"],
            "actual_margin": actual,
        })
    return out


def main():
    conn = db.connect()
    games = backtest.load_games(conn, "cfb", seasons=[2023, 2024, 2025],
                                require_line=True)
    print("Validating harness on %d CFB games with lines\n" % len(games))
    if len(games) < 500:
        sys.exit("Not enough games loaded to validate. Run the fetcher first.")

    # 1. Oracle — knows the answer. Must score essentially perfectly.
    m = metrics.evaluate(build(games, lambda g, a: a))
    check("oracle", m["ats_pct"] > 99.0,
          "ATS %.2f%% (expect ~100) ROI %+.1f%%" % (m["ats_pct"], m["roi"]))
    check("oracle-sig", m["ats_significant"] is True,
          "flagged as a proven edge: %s" % m["ats_significant"])

    # 2. Anti-oracle — sign flipped. Catches a convention error that would
    #    otherwise make a losing model look like a winner.
    m = metrics.evaluate(build(games, lambda g, a: g["market_margin"] - (a - g["market_margin"])))
    check("anti-oracle", m["ats_pct"] < 1.0,
          "ATS %.2f%% (expect ~0)" % m["ats_pct"])

    # 3. Market copy — no disagreement, so no bets should be graded at all.
    m = metrics.evaluate(build(games, lambda g, a: g["market_margin"]))
    check("market-copy", m["ats_n"] == 0,
          "%d bets placed (expect 0 — zero edge means no action)" % m["ats_n"])

    # 4. Modest real edge — the case that actually matters.
    #    A faint pull toward the true result BURIED IN NOISE. Without the noise
    #    term this is just a second oracle that scores 100%, which would prove
    #    nothing about the harness's ability to see a small, realistic edge.
    #    The assertion deliberately requires it to land well BELOW the oracle.
    rng_edge = random.Random(11)
    m = metrics.evaluate(build(
        games,
        lambda g, a: g["market_margin"] + 0.14 * (a - g["market_margin"]) + rng_edge.gauss(0, 9)))
    lo, hi = m["ats_ci95"]
    check("modest-edge", 52.38 < m["ats_pct"] < 70.0 and m["ats_significant"],
          "ATS %.2f%% CI %.1f-%.1f%% significant=%s (must beat break-even but stay well under the oracle)"
          % (m["ats_pct"], lo, hi, m["ats_significant"]))

    # 5. Pure noise — must land near 50% and must NOT be called significant.
    rng = random.Random(20260806)
    m = metrics.evaluate(build(
        games, lambda g, a: g["market_margin"] + rng.gauss(0, 7)))
    check("coin-flip", 46.0 < m["ats_pct"] < 54.0,
          "ATS %.2f%% (expect ~50)" % m["ats_pct"])
    check("noise-not-sig", not m["ats_significant"],
          "significant=%s (must be False — noise must never be called an edge)"
          % m["ats_significant"])

    # 6. Calibration detection — a model scaled 2x too big must be caught,
    #    since that is exactly the defect measured in the current model.
    m = metrics.evaluate(build(games, lambda g, a: 2.0 * g["market_margin"]))
    check("calib-detect", m["calib_slope"] is not None and 0.40 < m["calib_slope"] < 0.60,
          "slope %.3f on a deliberately 2x-inflated model (expect ~0.5)"
          % m["calib_slope"])

    print()
    if FAILURES:
        sys.exit("HARNESS INVALID — failed: %s" % ", ".join(FAILURES))
    print("All probes passed. The harness detects real edges, rejects noise,\n"
          "and measures calibration correctly — so its ~50% verdict on the\n"
          "Elo baseline is a finding about the model, not a bug in the test.")


if __name__ == "__main__":
    main()
