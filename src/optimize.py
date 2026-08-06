"""
optimize.py — search for the best parameters WITHOUT fooling ourselves.

This is the "test it a butt load of times" engine. The danger it is built to
survive is that searching hard over a fixed history will always find something
that looks brilliant and means nothing.

Four guards:

  1. TRAIN/TEST SPLIT. Parameters are chosen using only `--train` seasons and
     then scored once on `--test` seasons the search never saw. The test number
     is the only one anyone is allowed to quote.

  2. RMSE IS THE DEFAULT OBJECTIVE, NOT ATS%. ATS is a noisy binary outcome;
     optimizing it directly chases coin flips and overfits hard. Margin error
     is a dense, stable signal, and a model that predicts margins well wins ATS
     as a consequence. `--objective ats` exists but warns.

  3. OVERFIT GAP. Train and test scores are always reported together. A large
     gap means the parameters memorized the training seasons.

  4. COORDINATE DESCENT, NOT FULL GRID. Sweeping one parameter at a time and
     repeating converges in a few hundred evaluations instead of millions, and
     the smaller search space is itself an overfitting guard.

Usage:
    python3 src/optimize.py --sport cfb --train 2014-2021 --test 2022-2025
    python3 src/optimize.py --sport nfl --train 1999-2018 --test 2019-2025
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone

import backtest
import db
import metrics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Search space. Values are candidates, swept one parameter at a time.
SPACE_ELO = {
    "elo_per_point": [18.0, 20.0, 22.0, 25.0, 28.0, 31.0, 35.0],
    "scale":         [0.7, 0.85, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0],
    "hfa":           [0.0, 1.0, 1.8, 2.4, 3.0, 3.6, 4.2, 5.0],
    "elo_k":         [10.0, 14.0, 18.0, 22.0, 28.0, 34.0, 42.0],
    "elo_hfa":       [30.0, 45.0, 60.0, 75.0, 90.0],
    "elo_revert":    [0.0, 0.10, 0.20, 0.25, 0.33, 0.45, 0.60],
    "elo_fcs_rating":[1000.0, 1100.0, 1200.0, 1300.0],
    "totals_scale":  [0.94, 0.97, 1.0, 1.03, 1.06],
    "totals_prior_games": [1.0, 2.0, 3.0, 5.0, 8.0],
    "totals_carryover": [0.0, 0.15, 0.3, 0.5, 0.7, 1.0],
    "market_anchor": [0.0, 0.3, 0.5, 0.65, 0.75, 0.85, 0.92],
    "ppa_points_per_ppa": [35.0, 45.0, 55.0, 65.0, 75.0, 90.0],
    "ppa_prior_games": [2.0, 4.0, 6.0, 9.0],
    "ppa_carryover": [0.0, 0.2, 0.35, 0.5, 0.7],
}

# Only meaningful once Grant's grades are loaded.
SPACE_GRADES = {
    "sheet_coach_weight": [0.0, 0.5, 1.0, 1.5, 2.0, 3.0],
    "sheet_loss_sign":    [-1.0, 0.0, 1.0],
    "sheet_raw_wl":       [0.0, 0.5, 1.0, 1.5, 2.0],
    "grade_scale":   [0.8, 1.0, 1.15, 1.24, 1.4, 1.6, 1.8, 2.0, 2.4],
    "scale":         [0.8, 0.9, 1.0, 1.1, 1.25],
    "hfa":           [0.0, 1.0, 1.8, 2.4, 3.0, 3.6, 4.2],
    "quality_scale": [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0],
    "wq_top5":       [0.0, 2.0, 4.0, 6.0],
    "wq_top10":      [0.0, 1.5, 3.0, 4.5],
    "wq_top25":      [0.0, 1.0, 2.0, 3.0],
    "lq_unranked_fbs": [0.0, -1.0, -2.0, -4.0],
    "lq_fcs":        [0.0, -2.0, -4.0, -6.0],
}


def objective_value(preds, objective):
    """Lower is better."""
    m = metrics.evaluate(preds)
    if not m.get("n_games"):
        return float("inf"), m
    if objective == "rmse":
        return m["rmse"], m
    if objective == "mae":
        return m["mae"], m
    if objective == "ats":
        return -(m.get("ats_pct") or 0.0), m
    if objective == "roi":
        return -(m.get("roi") or -999.0), m
    if objective == "total_rmse":
        return m.get("total_rmse", float("inf")), m
    raise ValueError("unknown objective %r" % objective)


def evaluate_config(games, base_cfg, seasons, objective, grades):
    cfg = dict(base_cfg)
    cfg["_grades"] = grades
    preds = backtest.run(games, cfg, seasons)
    return objective_value(preds, objective)


def coordinate_descent(games, base_cfg, space, train_seasons, objective,
                       grades, rounds=3, verbose=True):
    cfg = dict(base_cfg)
    best, _ = evaluate_config(games, cfg, train_seasons, objective, grades)
    evals = 1
    if verbose:
        print("  start %s = %.5f" % (objective, best))

    for rnd in range(rounds):
        improved = False
        for param, candidates in space.items():
            cur = cfg.get(param)
            best_val, best_score = cur, best
            for v in candidates:
                if v == cur:
                    continue
                trial = dict(cfg)
                trial[param] = v
                score, _ = evaluate_config(games, trial, train_seasons, objective, grades)
                evals += 1
                if score < best_score - 1e-9:
                    best_score, best_val = score, v
            if best_val != cur:
                cfg[param] = best_val
                best = best_score
                improved = True
                if verbose:
                    print("    round %d  %-20s -> %-8s  %s=%.5f"
                          % (rnd + 1, param, best_val, objective, best))
        if not improved:
            if verbose:
                print("  converged after round %d" % (rnd + 1))
            break
    return cfg, best, evals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--train", default="2014-2021")
    ap.add_argument("--test", default="2022-2025")
    ap.add_argument("--rater", default="elo", choices=["elo", "grades", "blend"])
    ap.add_argument("--objective", default="rmse",
                    choices=["rmse", "mae", "ats", "roi", "total_rmse"])
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--save", help="write winning config to this JSON path")
    ap.add_argument("--init", help="seed the search from an existing config JSON")
    ap.add_argument("--only", help="comma-separated parameters to search (others held fixed)")
    args = ap.parse_args()

    if args.objective in ("ats", "roi"):
        print("WARNING: optimizing '%s' directly targets a noisy binary outcome and\n"
              "         overfits far more than 'rmse'. Treat the test number with\n"
              "         extra suspicion.\n" % args.objective)

    train = backtest.parse_seasons(args.train)
    test = backtest.parse_seasons(args.test)
    overlap = set(train) & set(test)
    if overlap:
        raise SystemExit("train and test seasons overlap: %s — that invalidates the test."
                         % sorted(overlap))

    conn = db.connect()
    games = backtest.load_games(conn, args.sport)
    grades = backtest.load_grades(conn, args.sport)
    if not games:
        raise SystemExit("No games for sport=%s. Run the fetcher first." % args.sport)
    if args.rater in ("grades", "blend") and not grades:
        print("NOTE: no grades loaded for %s — the '%s' rater will fall back to Elo.\n"
              "      Import Grant's sheets first for this to be meaningful.\n"
              % (args.sport, args.rater))

    space = dict(SPACE_ELO)
    if args.rater in ("grades", "blend"):
        space.update(SPACE_GRADES)
    if args.rater == "blend":
        space["blend_weight"] = [0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0]

    # Restricting the space lets the spread and totals halves be tuned in
    # separate passes without each undoing the other: the spread objective
    # (rmse) is blind to totals parameters, so a combined sweep would leave
    # them at defaults, and vice versa.
    if args.only:
        wanted = {p.strip() for p in args.only.split(",") if p.strip()}
        unknown = wanted - set(space)
        if unknown:
            raise SystemExit("--only names parameters not in the search space: %s"
                             % ", ".join(sorted(unknown)))
        space = {k: v for k, v in space.items() if k in wanted}

    base = {"rater": args.rater}
    if args.init:
        ipath = args.init if os.path.isabs(args.init) else os.path.join(ROOT, args.init)
        base.update(json.load(open(ipath)))
        base["rater"] = args.rater
        print("  seeded from %s" % ipath)
    print("Optimizing %s | rater=%s | objective=%s" % (args.sport, args.rater, args.objective))
    print("  train seasons: %s" % train)
    print("  test  seasons: %s  (never seen by the search)" % test)
    print("  %d games loaded\n" % len(games))

    t0 = time.time()
    best_cfg, train_score, evals = coordinate_descent(
        games, base, space, train, args.objective, grades, rounds=args.rounds)
    elapsed = time.time() - t0

    # ── the only number worth quoting ──
    train_preds = backtest.run(games, dict(best_cfg, _grades=grades), train)
    test_preds = backtest.run(games, dict(best_cfg, _grades=grades), test)
    m_train = metrics.evaluate(train_preds)
    m_test = metrics.evaluate(test_preds)

    print("\n%d configurations evaluated in %.1fs\n" % (evals, elapsed))
    print("Winning config:")
    for k in sorted(best_cfg):
        if not k.startswith("_"):
            print("    %-22s %s" % (k, best_cfg[k]))

    print("\n" + metrics.format_report(m_train, "IN-SAMPLE (train %s) — do not quote" % args.train))
    print("\n" + metrics.format_report(m_test, "OUT-OF-SAMPLE (test %s) — the real result" % args.test))

    gap = m_test["rmse"] - m_train["rmse"]
    print("\n  overfit gap (test RMSE - train RMSE): %+.3f pts" % gap)
    if gap > 0.75:
        print("     -> LARGE. The parameters partly memorized the training seasons.")
    else:
        print("     -> acceptable; the fit generalizes.")

    conn.execute(
        """INSERT INTO backtest_runs
           (created_at, sport, label, config_json, train_seasons, test_seasons,
            n_games, ats_pct, su_pct, mae, rmse, calib_slope, roi)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now(timezone.utc).isoformat(), args.sport,
         "opt-%s-%s" % (args.rater, args.objective),
         json.dumps({k: v for k, v in best_cfg.items() if not k.startswith("_")}),
         args.train, args.test, m_test.get("n_games"), m_test.get("ats_pct"),
         m_test.get("su_pct"), m_test.get("mae"), m_test.get("rmse"),
         m_test.get("calib_slope"), m_test.get("roi")))
    conn.commit()

    if args.save:
        path = args.save if os.path.isabs(args.save) else os.path.join(ROOT, args.save)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Save the FULL merged config, not just the parameters the search moved.
        # A partial file silently relies on whatever the defaults happen to be
        # at read time, which makes an old result impossible to reproduce after
        # any default changes.
        import engine as _engine
        full = {k: v for k, v in _engine.merge_config(
            {k: v for k, v in best_cfg.items() if not k.startswith("_")}).items()}
        with open(path, "w") as fh:
            json.dump(full, fh, indent=2, sort_keys=True)
        print("\n  config saved -> %s" % path)


if __name__ == "__main__":
    main()
