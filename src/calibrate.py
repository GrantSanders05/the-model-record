"""
calibrate.py — re-fit the one number that says how many points a rating is worth.

`scale` multiplies the rating difference on its way to a predicted margin. It is
the difference between "Georgia is 8 rating points better" and "Georgia wins by
8", and it is the only parameter here that can be derived rather than searched:
regress actual margin on predicted margin and the slope IS the correction. A slope
of 1.16 means every prediction is 16% too small.

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
    python3 src/calibrate.py --season 2025 --apply       # writes config/cfb_grades.json
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


def main():
    ap = argparse.ArgumentParser(description="Re-fit the rating-to-points scale.")
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--config", default="config/cfb_grades.json")
    ap.add_argument("--split-week", type=int, default=8)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    path = args.config if os.path.isabs(args.config) else os.path.join(ROOT, args.config)
    config = json.load(open(path))
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
