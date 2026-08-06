"""
bakeoff.py — every approach, same games, honest out-of-sample.

Parameters are fitted on TRAIN seasons and every number reported comes from
TEST seasons the search never saw. Confidence intervals are bootstrapped over
games, so "which model is best" is answered with an interval rather than a
ranking of point estimates that differ by noise.

Contenders:
    market      the closing line itself — the benchmark everything must beat
    elo         results-only power rating
    ppa         opponent-adjusted per-play efficiency (the SP+ family)
    ppa+anchor  the same, shrunk toward the closing line
    elo+anchor  results-only, shrunk toward the closing line

Usage:
    python3 src/bakeoff.py --train 2014-2020 --test 2021-2025
"""

import argparse
import json
import os
import random

import backtest
import db
import metrics
import optimize
import pro_models

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bootstrap_ats(preds, iters=2000, seed=7):
    """
    Resample games with replacement to get an interval on ATS%.

    More honest than a single binomial CI when comparing several models,
    because it reflects the actual game-level variation in this sample.
    """
    rows = []
    for p in preds:
        if p.get("market_margin") is None:
            continue
        e = p["pred_margin"] - p["market_margin"]
        d = p["actual_margin"] - p["market_margin"]
        if e == 0 or d == 0:
            continue
        rows.append(1 if (e > 0) == (d > 0) else 0)
    if len(rows) < 30:
        return None
    rng = random.Random(seed)
    n = len(rows)
    outs = []
    for _ in range(iters):
        s = sum(rows[rng.randrange(n)] for _ in range(n))
        outs.append(100.0 * s / n)
    outs.sort()
    return {"n": n, "mean": 100.0 * sum(rows) / n,
            "lo": outs[int(0.025 * iters)], "hi": outs[int(0.975 * iters)]}


def evaluate(games, cfg, test, grades, stats, edge=0.0):
    c = dict(cfg)
    c["_grades"] = grades
    c["_stats"] = stats
    preds = backtest.run(games, c, test)
    return preds, metrics.evaluate(preds, edge_threshold=edge)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--train", default="2014-2020")
    ap.add_argument("--test", default="2021-2025")
    ap.add_argument("--edge", type=float, default=0.0,
                    help="minimum disagreement with the market to place a bet")
    args = ap.parse_args()

    train = backtest.parse_seasons(args.train)
    test = backtest.parse_seasons(args.test)
    conn = db.connect()
    games = backtest.load_games(conn, args.sport)
    grades = backtest.load_grades(conn, args.sport)
    stats = pro_models.load_stats(conn, args.sport)
    print("Bake-off | train %s | test %s | %d games, %d efficiency rows\n"
          % (args.train, args.test, len(games), len(stats)))

    # Fit each contender on TRAIN only.
    fitted = {}
    specs = [
        ("elo", {"rater": "elo"},
         ["elo_per_point", "scale", "hfa", "elo_k", "elo_hfa", "elo_revert"]),
        ("ppa", {"rater": "ppa"},
         ["ppa_points_per_ppa", "scale", "hfa", "ppa_prior_games", "ppa_carryover"]),
        ("elo+anchor", {"rater": "elo"},
         ["elo_per_point", "scale", "hfa", "elo_k", "market_anchor"]),
        ("ppa+anchor", {"rater": "ppa"},
         ["ppa_points_per_ppa", "scale", "hfa", "ppa_carryover", "market_anchor"]),
    ]
    for name, base, params in specs:
        space = {k: v for k, v in optimize.SPACE_ELO.items() if k in params}
        print("  fitting %-12s ..." % name, end="", flush=True)
        cfg, score, evals = optimize.coordinate_descent(
            games, base, space, train, "rmse", grades, rounds=2, verbose=False)
        cfg["_stats"] = stats
        fitted[name] = cfg
        print(" %d configs, train RMSE %.3f" % (evals, score))

    print("\n%-14s %6s %8s %16s %9s %8s %8s" % (
        "model", "bets", "ATS%", "95% CI (boot)", "ROI%", "MAE", "anchor"))
    print("-" * 76)

    # The market itself: predict exactly the line. It never disagrees, so it
    # places no bets -- it is here as the accuracy benchmark, not a strategy.
    mk_preds, _ = evaluate(games, {"rater": "elo", "market_anchor": 1.0}, test, grades, stats)
    mk = metrics.evaluate(mk_preds)
    print("%-14s %6s %8s %16s %9s %8.2f %8s"
          % ("market (line)", "—", "—", "—", "—", mk["mae"], "1.00"))

    results = {}
    for name in ("elo", "ppa", "elo+anchor", "ppa+anchor"):
        cfg = dict(fitted[name])
        stats_ref = cfg.pop("_stats", stats)
        preds, m = evaluate(games, cfg, test, grades, stats_ref, edge=args.edge)
        bs = bootstrap_ats(preds)
        results[name] = (m, bs, cfg)
        ci = "%.1f – %.1f" % (bs["lo"], bs["hi"]) if bs else "—"
        print("%-14s %6d %8.2f %16s %9s %8.2f %8.2f" % (
            name, m.get("ats_n", 0), m.get("ats_pct") or 0, ci,
            "%+.2f" % m["roi"] if m.get("roi") is not None else "—",
            m["mae"], cfg.get("market_anchor", 0.0)))

    print("\n  break-even 52.38%%  ·  bets counted only where |model − line| ≥ %.1f" % args.edge)

    best = max(results.items(), key=lambda kv: (kv[1][0].get("ats_pct") or 0))
    name, (m, bs, cfg) = best
    print("\n  Best out-of-sample: %s at %.2f%% ATS" % (name, m.get("ats_pct") or 0))
    if bs and bs["lo"] > 52.38:
        print("  Its entire bootstrap interval clears break-even — a real edge.")
    else:
        print("  Its interval still includes break-even — NOT a proven edge.")
        print("  Betting real money on this expects to lose to the juice.")

    out = os.path.join(ROOT, "output", "bakeoff.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump({k: {"ats_pct": v[0].get("ats_pct"), "roi": v[0].get("roi"),
                       "n": v[0].get("ats_n"), "ci": v[1],
                       "config": {kk: vv for kk, vv in v[2].items() if not kk.startswith("_")}}
                   for k, v in results.items()}, fh, indent=2)
    print("\n  detail -> %s" % out)


if __name__ == "__main__":
    main()
