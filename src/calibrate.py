"""
calibrate.py — re-fit the one number that says how many points a rating is worth.

`scale` multiplies the rating difference on its way to a predicted margin. It is
the difference between "Georgia is 8 rating points better" and "Georgia wins by
8", and it is the only parameter here that can be derived rather than searched:
regress actual margin on predicted margin and the slope IS the correction. A slope
of 1.16 means every prediction is 16% too small.

ONE SLOPE WAS NOT ENOUGH, and the way it failed is worth keeping. Under
`grade_formula: computed` the rating is two quantities glued together: position
grades, which are fixed from the day Grant grades a team, and quality points,
which start every season at zero and accumulate. `scale` multiplied their sum, so
one number had to serve both -- and it served neither. Measured on 2025 the
calibration slope ran 1.090 over weeks 1-4 and 0.797 over weeks 11+: every
early-season line too small, every late-season line too big, and a full-season
slope of 0.870 that looked like a mild correction while hiding a swing of nearly
0.3 between the ends of the season.

Regressed separately the two are not close: a position-grade point is worth 1.31
points of margin, a quality point 0.61. `--two` fits both. It needs no new
parameter, because `quality_scale` already multiplies the quality half INSIDE the
rating and `scale` multiplies the sum, so the pair (scale, quality_scale) is
exactly (a, b/a).

    python3 src/calibrate.py --season 2025 --two

WHY IT MATTERS BEYOND ACCURACY. The margin becomes a win probability, the
probability becomes an expected value, and the EV becomes a Kelly stake. An
under-dispersed margin understates how often the favourite wins, so it
systematically under-stakes favourites and over-stakes dogs. Calibration is not
cosmetic here; it is the input to the money.

HOW THIS AVOIDS FOOLING ITSELF. Fitting a parameter on a season and then reporting
that season's improvement is circular. So the fit is done on the first part of the
season and scored on the rest, which the fit never saw. On 2025 that was 54.15% ->
57.71% ATS across 350 held-out games — encouraging, and still one season.

    python3 src/calibrate.py --season 2025
    python3 src/calibrate.py --season 2025 --two --apply  # writes config/cfb_grades.json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest      # noqa: E402
import db            # noqa: E402
import metrics       # noqa: E402
import pro_models    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ats(preds, scale):
    w = n = 0
    for p in preds:
        if p["market_margin"] is None:
            continue
        edge = p["pred_margin"] * scale - p["market_margin"]
        diff = p["actual_margin"] - p["market_margin"]
        if edge == 0 or diff == 0:
            continue
        n += 1
        w += (edge > 0) == (diff > 0)
    return (100.0 * w / n if n else None), n


def fit(conn, sport, season, config, split_week=8):
    """-> report dict. Fits on weeks <= split_week, scores on the rest."""
    cfg = dict(config)
    if cfg.get("rater") in ("grades", "blend"):
        cfg["_grades"] = backtest.load_grades(conn, sport)
        cfg["_stats"] = pro_models.load_stats(conn, sport)
    # Fit at scale 1.0 so the slope IS the correction rather than a correction to
    # a correction. Whatever is in the config is deliberately ignored here.
    cfg["scale"] = 1.0
    ship_cfg = dict(cfg)                    # kept before run() consumes the _grades keys
    preds = backtest.run(backtest.load_games(conn, sport), cfg, test_seasons=[season])
    if not preds:
        raise SystemExit("no predictions for %s %s — are the grades loaded?"
                         % (sport, season))

    train = [p for p in preds if (p["week"] or 0) <= split_week]
    test = [p for p in preds if (p["week"] or 0) > split_week]
    full_slope = metrics.linreg([p["pred_margin"] for p in preds],
                                [p["actual_margin"] for p in preds])[0]
    # HOW MUCH OF THE DISAGREEMENT IS REAL, which is a different question from how
    # big the numbers should be. Regress the realised edge (actual - market) on the
    # claimed edge (model - market): the slope is the share that shows up. On 2025
    # it is 0.135, so an eleven-point disagreement is worth about a point and a half.
    # Win probability and EV have to be computed from the shrunk number or a
    # 20-point underdog prices at +196% and tops the board.
    # Measured at the scale that will SHIP, not at 1.0. The claimed edge is
    # `scale * raw - market`, and the market term does not scale with it, so the two
    # fits are not interchangeable: 0.096 at scale 1.0 against 0.135 at scale 1.161.
    # Fitting the shrink against a model nobody runs would ship the wrong number.
    scaled = dict(ship_cfg)
    scaled["scale"] = round(full_slope, 3) if full_slope else 1.0
    at_ship = backtest.run(backtest.load_games(conn, sport), scaled,
                           test_seasons=[season])
    priced = [p for p in at_ship if p["market_margin"] is not None]
    edge_slope = None
    if len(priced) > 100:
        edge_slope = metrics.linreg(
            [p["pred_margin"] - p["market_margin"] for p in priced],
            [p["actual_margin"] - p["market_margin"] for p in priced])[0]
    out = {"n": len(preds), "season": season, "full_slope": full_slope,
           "recommended": round(full_slope, 3) if full_slope else None,
           "edge_realised": round(edge_slope, 3) if edge_slope else None,
           "edge_n": len(priced)}
    if len(train) > 100 and len(test) > 100:
        tr = metrics.linreg([p["pred_margin"] for p in train],
                            [p["actual_margin"] for p in train])[0]
        base, n = _ats(test, 1.0)
        tuned, _ = _ats(test, tr)
        out.update({"train_n": len(train), "test_n": n, "train_slope": tr,
                    "held_out_ats_before": base, "held_out_ats_after": tuned})
    return out


def _solve(A, B):
    """Gaussian elimination on a small symmetric system. No numpy dependency."""
    k = len(B)
    M = [A[r][:] + [B[r]] for r in range(k)]
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        if abs(M[c][c]) < 1e-12:
            raise SystemExit("the two rating halves are collinear — nothing to fit.")
        for r in range(k):
            if r == c:
                continue
            f = M[r][c] / M[c][c]
            for j in range(c, k + 1):
                M[r][j] -= f * M[c][j]
    return [M[r][k] / M[r][r] for r in range(k)]


def _ols(rows, keys, target, offset):
    """Least squares of (target - offset) on `keys`, no intercept."""
    n = len(rows)
    k = len(keys)
    A = [[sum(r[keys[a]] * r[keys[b]] for r in rows) for b in range(k)] for a in range(k)]
    B = [sum(r[keys[a]] * (r[target] - offset(r)) for r in rows) for a in range(k)]
    return _solve(A, B)


def _mae(rows, a, b, hfa):
    e = [abs(r["actual_margin"] - (a * r["grade_diff"] + b * r["quality_diff"]
                                   + hfa * r["is_home"])) for r in rows]
    return sum(e) / len(e)


def _slope(rows, a, b, hfa):
    P = [a * r["grade_diff"] + b * r["quality_diff"] + hfa * r["is_home"] for r in rows]
    A = [r["actual_margin"] for r in rows]
    return metrics.linreg(P, A)[0]


def _flips(rows, old, new, hfa):
    """How many SIDES change, and what those games did. See the note in main()."""
    def edge(r, a, b):
        return a * r["grade_diff"] + b * r["quality_diff"] + hfa * r["is_home"] - r["market_margin"]
    w = n = flipped = 0
    for r in rows:
        if r["market_margin"] is None:
            continue
        e0, e1 = edge(r, *old), edge(r, *new)
        if (e0 > 0) == (e1 > 0):
            continue
        flipped += 1
        d = r["actual_margin"] - r["market_margin"]
        if d == 0:
            continue
        n += 1
        w += (e1 > 0) == (d > 0)
    return flipped, w, n - w


def fit_two(conn, sport, season, config, split_week=8):
    """
    Fit `scale` and `quality_scale` separately. -> report dict.

    Home field is PINNED, not fitted. It was measured on 8,364 games; these are
    eight hundred. Letting a season of data move a constant that has ten years
    behind it is how a fit talks itself into noise.
    """
    if config.get("grade_formula") != "computed":
        raise SystemExit("--two needs grade_formula 'computed'; this config has %r. "
                         "Under 'sheet' the quality points are inside the snapshot "
                         "and the two halves cannot be separated."
                         % config.get("grade_formula"))
    cfg = dict(config)
    cfg["_grades"] = backtest.load_grades(conn, sport)
    cfg["_stats"] = pro_models.load_stats(conn, sport)
    hfa = float(config.get("hfa", 4.0))
    preds = backtest.run(backtest.load_games(conn, sport), cfg, test_seasons=[season])
    rows = [p for p in preds if "grade_diff" in p and p["actual_margin"] is not None]
    if len(rows) < 200:
        raise SystemExit("only %d fully-graded games in %d — not enough to fit two "
                         "coefficients." % (len(rows), season))

    def off(r):
        return hfa * r["is_home"]

    a, b = _ols(rows, ["grade_diff", "quality_diff"], "actual_margin", off)
    # The same fit forced to use ONE coefficient, so the report can show what is
    # gained by splitting rather than only what the split scores.
    for r in rows:
        r["_sum"] = r["grade_diff"] + r["quality_diff"]
    a1, = _ols(rows, ["_sum"], "actual_margin", off)

    old = (config.get("scale", 1.0), config.get("scale", 1.0) * config.get("quality_scale", 1.0))
    early = [r for r in rows if (r["week"] or 0) <= 4]
    late = [r for r in rows if (r["week"] or 0) >= 11]
    flipped, fw, fl = _flips(rows, old, (a, b), hfa)

    # The paired difference uses EVERY game and a continuous error, so it has the
    # power the ATS comparison does not: only the games whose side changes carry
    # any ATS information at all, and there are a hundred of those.
    d = [abs(r["actual_margin"] - (old[0] * r["grade_diff"] + old[1] * r["quality_diff"] + off(r)))
         - abs(r["actual_margin"] - (a * r["grade_diff"] + b * r["quality_diff"] + off(r)))
         for r in rows]
    mean_d = sum(d) / len(d)
    var = sum((x - mean_d) ** 2 for x in d) / (len(d) - 1)
    se = (var / len(d)) ** 0.5

    out = {"n": len(rows), "season": season, "hfa": hfa,
           "scale": round(a, 3), "quality_scale": round(b / a, 3),
           "one_coef": round(a1, 3),
           "slope_old": _slope(rows, *old, hfa), "slope_new": _slope(rows, a, b, hfa),
           "early_old": _slope(early, *old, hfa), "early_new": _slope(early, a, b, hfa),
           "late_old": _slope(late, *old, hfa), "late_new": _slope(late, a, b, hfa),
           "mae_old": _mae(rows, *old, hfa), "mae_new": _mae(rows, a, b, hfa),
           "mae_gain": mean_d, "mae_se": se, "mae_t": mean_d / se if se else 0.0,
           "flipped": flipped, "flip_w": fw, "flip_l": fl}

    train = [r for r in rows if (r["week"] or 0) <= split_week]
    test = [r for r in rows if (r["week"] or 0) > split_week]
    if len(train) > 150 and len(test) > 150:
        ta, tb = _ols(train, ["grade_diff", "quality_diff"], "actual_margin", off)
        out.update({"train_n": len(train), "test_n": len(test),
                    "train_scale": ta, "train_quality_scale": tb / ta,
                    "test_mae_old": _mae(test, *old, hfa),
                    "test_mae_new": _mae(test, ta, tb, hfa)})
    return out


def fit_fallback(conn, sport, season, config):
    """
    Fit `fallback_scale` — the multiplier for games the grade rater could not answer.

    Those are predicted by Elo: a different rater, with its own units and its own
    mean offset. A scale fitted on graded games has nothing to say about them, and
    sharing one is not neutral — when `scale` moved 0.997 -> 1.311 on the strength
    of 808 graded games, the 126 borrowed ones were silently re-tuned with it.

    Home field is pinned, as in fit_two, and stripped back off before fitting so
    the slope is the multiplier itself.
    """
    cfg = dict(config)
    cfg["_grades"] = backtest.load_grades(conn, sport)
    cfg["_stats"] = pro_models.load_stats(conn, sport)
    hfa = float(config.get("hfa", 4.0))
    neutral = float(config.get("neutral_hfa", 0.0))
    cfg["fallback_scale"] = 1.0          # so the slope IS the multiplier
    preds = backtest.run(backtest.load_games(conn, sport), cfg, test_seasons=[season])
    rows = [p for p in preds if p.get("borrowed") and p["actual_margin"] is not None]
    if len(rows) < 60:
        raise SystemExit("only %d borrowed games in %d — too few to fit a scale for "
                         "them. Leave fallback_scale unset and it follows `scale`."
                         % (len(rows), season))
    home = lambda p: hfa * p["is_home"] + neutral * (1.0 - p["is_home"])
    x = [p["pred_margin"] - home(p) for p in rows]
    y = [p["actual_margin"] - home(p) for p in rows]
    slope = metrics.linreg(x, y)[0]
    err = lambda k: sum(abs(y[i] - k * x[i]) for i in range(len(x))) / len(x)
    return {"fallback_scale": round(slope, 3), "n": len(rows),
            "current": config.get("fallback_scale", config.get("scale", 1.0)),
            "mae_current": err(config.get("fallback_scale", config.get("scale", 1.0))),
            "mae_fitted": err(slope)}


def _main_fallback(args, path, config):
    r = fit_fallback(db.connect(), args.sport, args.season, config)
    print("\n── fallback_scale, fitted on the games the grades could not answer ──")
    print("  borrowed games         : %d" % r["n"])
    print("  currently effective    : %.3f" % r["current"])
    print("  fitted                 : %.3f" % r["fallback_scale"])
    print("  error per game         : %.2f -> %.2f points" % (r["mae_current"], r["mae_fitted"]))
    if r["mae_fitted"] >= r["mae_current"]:
        print("    -> NO IMPROVEMENT. Leave it unset and it follows `scale`.")
    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to write it to %s." % args.config)
        return
    if r["mae_fitted"] >= r["mae_current"]:
        raise SystemExit("refusing to apply: the fit did not beat what ships.")
    config["fallback_scale"] = r["fallback_scale"]
    with open(path, "w") as fh:
        json.dump(config, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("\nwrote fallback_scale = %.3f to %s" % (r["fallback_scale"], args.config))


def fit_edge(conn, sport, season, config):
    """
    How much of the model's disagreement with the market actually shows up.

    Measured at the config EXACTLY as it ships — not at scale 1.0 and not at a
    scale this run recommends. The claimed edge is `prediction - market` and the
    market term does not move when the scale does, so the two are different
    regressions and only one of them is about the model anybody runs.
    """
    cfg = dict(config)
    if cfg.get("rater") in ("grades", "blend"):
        cfg["_grades"] = backtest.load_grades(conn, sport)
        cfg["_stats"] = pro_models.load_stats(conn, sport)
    preds = backtest.run(backtest.load_games(conn, sport), cfg, test_seasons=[season])
    priced = [p for p in preds if p["market_margin"] is not None]
    if len(priced) < 100:
        raise SystemExit("only %d priced games in %d — not enough." % (len(priced), season))
    slope = metrics.linreg([p["pred_margin"] - p["market_margin"] for p in priced],
                           [p["actual_margin"] - p["market_margin"] for p in priced])[0]
    return {"edge_realised": round(slope, 3), "n": len(priced),
            "at_scale": config.get("scale", 1.0),
            "at_quality_scale": config.get("quality_scale", 1.0)}


def _main_edge(args, path, config):
    r = fit_edge(db.connect(), args.sport, args.season, config)
    print("\n── edge_realised, measured at the shipping config ──")
    print("  scale %.3f, quality_scale %.3f, on %d priced games"
          % (r["at_scale"], r["at_quality_scale"], r["n"]))
    print("  edge_realised          : %.3f   (config has %.3f)"
          % (r["edge_realised"], config.get("edge_realised", 1.0)))
    print("    -> a 10-point disagreement is worth %.1f points."
          % (10 * r["edge_realised"]))
    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to write it to %s." % args.config)
        return
    config["edge_realised"] = r["edge_realised"]
    with open(path, "w") as fh:
        json.dump(config, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("\nwrote edge_realised = %.3f to %s" % (r["edge_realised"], args.config))


def _main_two(args, path, config):
    r = fit_two(db.connect(), args.sport, args.season, config, args.split_week)
    print("\n── scale AND quality_scale, re-fit on %s %d ──" % (args.sport, args.season))
    print("  fully-graded games     : %d   (home field pinned at %.2f, not fitted)"
          % (r["n"], r["hfa"]))
    print("  one coefficient        : scale %.3f for both halves" % r["one_coef"])
    print("  two coefficients       : scale %.3f   quality_scale %.3f"
          % (r["scale"], r["quality_scale"]))
    print("    -> a position-grade point is worth %.2f points of margin, a quality "
          "point %.2f." % (r["scale"], r["scale"] * r["quality_scale"]))

    # The two ends of the season are the point. A full-season slope near 1.0 can
    # be an average of "too small all September" and "too big all November".
    print("\n  calibration slope (1.000 = correctly scaled):")
    print("    %-22s %8s %8s" % ("", "current", "fitted"))
    for label, prefix in (("full season", "slope"), ("weeks 1-4", "early"),
                          ("weeks 11+", "late")):
        print("    %-22s %8.3f %8.3f"
              % (label, r[prefix + "_old"], r[prefix + "_new"]))

    print("\n  average error per game : %.2f -> %.2f points" % (r["mae_old"], r["mae_new"]))
    print("    improvement          : %.3f +/- %.3f   t = %.2f" 
          % (r["mae_gain"], r["mae_se"], r["mae_t"]))
    if r["mae_t"] < 2:
        print("    -> NOT SIGNIFICANT. Do not apply this.")

    # WHY ATS IS REPORTED THIS WAY. A side only carries information if it CHANGED,
    # and comparing two whole-season ATS percentages hides how few did. On 2025 a
    # 0.3-point swing in the headline came from a hundred games going 54-52.
    print("\n  sides that actually change: %d of %d, and they went %d-%d"
          % (r["flipped"], r["n"], r["flip_w"], r["flip_l"]))
    print("    -> the ATS difference between these two configs is %d coin flips."
          % (r["flip_w"] + r["flip_l"]))

    if "test_n" in r:
        print("\n  held out — fit on weeks 1-%d, scored on the rest:" % args.split_week)
        print("    fitted on train      : scale %.3f  quality_scale %.3f"
              % (r["train_scale"], r["train_quality_scale"]))
        print("    test error per game  : %.3f -> %.3f  (%d games)"
              % (r["test_mae_old"], r["test_mae_new"], r["test_n"]))
        if r["test_mae_new"] >= r["test_mae_old"]:
            print("    -> NO IMPROVEMENT out of sample. Do not apply this.")

    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to write it to %s." % args.config)
        return
    if r["mae_t"] < 2 or r.get("test_mae_new", 0) >= r.get("test_mae_old", 1):
        raise SystemExit("refusing to apply: the fit did not clear both gates above.")
    config["scale"] = r["scale"]
    config["quality_scale"] = r["quality_scale"]
    with open(path, "w") as fh:
        json.dump(config, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("\nwrote scale = %.3f, quality_scale = %.3f to %s"
          % (r["scale"], r["quality_scale"], args.config))
    print("NOW RE-FIT edge_realised: it is measured at the shipping scale and the "
          "scale just moved.\n  python3 src/calibrate.py --season %d --edge-only --apply"
          % args.season)


def main():
    ap = argparse.ArgumentParser(description="Re-fit the rating-to-points scale.")
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--config", default="config/cfb_grades.json")
    ap.add_argument("--split-week", type=int, default=8)
    ap.add_argument("--two", action="store_true",
                    help="fit scale and quality_scale separately")
    ap.add_argument("--edge-only", action="store_true",
                    help="re-fit only edge_realised, at the config as it ships")
    ap.add_argument("--fallback", action="store_true",
                    help="fit fallback_scale on the games the grade rater could not answer")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    path = args.config if os.path.isabs(args.config) else os.path.join(ROOT, args.config)
    config = json.load(open(path))
    if args.two:
        return _main_two(args, path, config)
    if args.edge_only:
        return _main_edge(args, path, config)
    if args.fallback:
        return _main_fallback(args, path, config)
    # A single scale cannot express a config whose two rating halves have already
    # been given their own coefficients: applying one here would silently fold
    # quality_scale back into a number fitted as if it did not exist.
    if args.apply and config.get("quality_scale", 1.0) != 1.0:
        raise SystemExit(
            "this config has quality_scale %.3f, so `scale` is half of a "
            "two-coefficient fit. Re-fit both with --two, or write just the edge "
            "shrink with --edge-only." % config["quality_scale"])
    r = fit(db.connect(), args.sport, args.season, config, args.split_week)

    print("\n── scale, re-fit on %s %d ──" % (args.sport, args.season))
    print("  predictions            : %d" % r["n"])
    print("  calibration slope      : %.3f   (1.000 = correctly scaled)"
          % (r["full_slope"] or 0))
    print("  current scale in config: %.3f" % config.get("scale", 1.0))
    print("  recommended scale      : %.3f" % (r["recommended"] or 1.0))
    if r.get("edge_realised") is not None:
        print("\n  share of the disagreement that is realised, on %d priced games:"
              % r["edge_n"])
        print("    edge_realised        : %.3f   (config has %.3f)"
              % (r["edge_realised"], config.get("edge_realised", 1.0)))
        print("    -> a 10-point disagreement is worth %.1f points."
              % (10 * r["edge_realised"]))
    if "test_n" in r:
        print("\n  held out, to keep this honest — fit on weeks 1-%d, scored on the rest:"
              % args.split_week)
        print("    fitted slope         : %.3f  (from %d games)"
              % (r["train_slope"], r["train_n"]))
        print("    held-out ATS before  : %.2f%%  (%d games)"
              % (r["held_out_ats_before"], r["test_n"]))
        print("    held-out ATS after   : %.2f%%" % r["held_out_ats_after"])
        if r["held_out_ats_after"] <= r["held_out_ats_before"]:
            print("    -> NO IMPROVEMENT out of sample. Do not apply this.")

    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to write it to %s." % args.config)
        return
    config["scale"] = r["recommended"]
    if r.get("edge_realised") is not None:
        config["edge_realised"] = r["edge_realised"]
    with open(path, "w") as fh:
        json.dump(config, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("\nwrote scale = %.3f to %s" % (r["recommended"], args.config))


if __name__ == "__main__":
    main()
