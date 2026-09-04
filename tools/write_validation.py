"""
write_validation.py — freeze the walk-forward validation result into the repo.

The public page leads its evidence with a replay of the last complete season on
Grant's film grades. CI cannot recompute it: the runner restores a database holding
only the current season's grades, because the historical ones were imported from a
workbook that lives on Grant's machine. Without a committed summary the page simply
drops the section, which is what it did.

WHAT IS COMMITTED IS SIX NUMBERS. No grades, no per-team ratings, nothing that
would put the moat in a public repo — the arithmetic is public by design and the
film is not.

IT CARRIES A FINGERPRINT OF THE CONFIG IT WAS COMPUTED UNDER. Change `scale`, the
rater or any other parameter that moves a prediction and the fingerprint stops
matching, the page says the figure is from an older configuration, and the QA suite
says so too. A frozen number with no way to notice it has gone stale is worse than
no number.

    python3 tools/write_validation.py --season 2025
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import db            # noqa: E402
import run_update    # noqa: E402

OUT_DIR = os.path.join(ROOT, "data", "validation")

# The parameters that change a prediction. A fingerprint over the whole config would
# churn on cosmetic keys; this is the set that actually moves a number.
FINGERPRINT_KEYS = ["rater", "scale", "edge_realised", "hfa", "neutral_hfa", "grade_scale",
                    "grade_formula", "quality_scale", "blend_weight",
                    "sheet_coach_weight", "sheet_loss_sign", "sheet_raw_wl",
                    "wq_top5", "wq_top10", "wq_top25", "wq_other",
                    "lq_fcs", "lq_ranked", "lq_unranked_fbs"]


def fingerprint(config):
    payload = json.dumps({k: config.get(k) for k in FINGERPRINT_KEYS}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--config", default="config/cfb_grades.json")
    args = ap.parse_args()

    path = args.config if os.path.isabs(args.config) else os.path.join(ROOT, args.config)
    config = json.load(open(path))
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) c FROM grades WHERE sport=? AND season=?",
                     (args.sport, args.season)).fetchone()["c"]
    if not n:
        raise SystemExit(
            "no %d grades in this database — run this where the historical workbook "
            "has been imported, not on a CI runner." % args.season)

    v = run_update.validation_backtest(conn, args.sport, args.season + 1, config)
    if not v:
        raise SystemExit("the backtest produced nothing for %d" % args.season)
    v["config_fingerprint"] = fingerprint(config)
    v["computed_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    v["config"] = {k: config.get(k) for k in FINGERPRINT_KEYS}

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "%s_%d.json" % (args.sport, args.season))
    with open(out, "w") as fh:
        json.dump(v, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("wrote %s" % out)
    print("  %.2f%% ATS on %d games (95%% CI %.1f-%.1f), ROI %+.2f%%"
          % (v["ats_pct"], v["n"], v["ci_lo"], v["ci_hi"], v["roi"]))
    if "offered_ats_pct" in v:
        print("  %.2f%% ATS on the %d it would have OFFERED, ROI %+.2f%%   "
              "(|line| <= %.0f; the rest are marked no_bet and never staked)"
              % (v["offered_ats_pct"], v["offered_n"], v["offered_roi"],
                 v["blowout_line"]))
    print("  fingerprint %s — regenerate this whenever the config changes."
          % v["config_fingerprint"])


if __name__ == "__main__":
    main()
