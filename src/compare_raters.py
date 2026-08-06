"""
compare_raters.py — does the film grading beat a machine that only reads results?

This is the question the brand rests on. Everything else is arithmetic.

All variants are scored on the SAME set of games: only weeks where a real grade
snapshot exists, so nothing silently falls back to Elo and gets compared against
itself. Variants:

    sheet    Grant's spreadsheet reproduced exactly, bugs included
    fixed    same grades, three structural corrections applied
    elo      results-only baseline, no grades at all
    blend    Elo + graded rating, weighted

Reported side by side with calibration slope, because a model can rank teams
correctly and still lose money by systematically overstating margins.

Usage:
    python3 src/compare_raters.py --season 2025 --weeks 1-9
"""

import argparse
import json

import backtest
import db
import engine
import metrics


def eligible_game_ids(games, grades, season, weeks):
    """
    Games where BOTH teams have a grade snapshot available.

    Without this filter the graded variants silently fall back to Elo on any
    game involving an ungraded team (FCS opponents, or a name the sheet spells
    differently) and get compared against a model that is partly just Elo.
    On the first run that was 30% of the sample.
    """
    graded = {t for (s, t) in grades if s == season}
    ok, skipped = set(), 0
    for g in games:
        if g["season"] != season or g["week"] not in weeks:
            continue
        if g["home_score"] is None or g["market_margin"] is None:
            continue
        if g["home_team"] in graded and g["away_team"] in graded:
            ok.add(g["game_id"])
        else:
            skipped += 1
    return ok, skipped


def run_variant(games, cfg, season, weeks, grades, eligible):
    c = dict(cfg)
    c["_grades"] = grades
    preds = backtest.run(games, c, [season])
    return [p for p in preds if p["week"] in weeks and p["game_id"] in eligible]


def parse_weeks(spec):
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return set(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--weeks", default="1-9")
    ap.add_argument("--base-config", default="config/cfb_elo.json")
    args = ap.parse_args()

    conn = db.connect()
    games = backtest.load_games(conn, args.sport)
    grades = backtest.load_grades(conn, args.sport)
    weeks = parse_weeks(args.weeks)

    if not grades:
        raise SystemExit("No grades imported. Run import_workbook.py first.")

    import os
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bp = os.path.join(ROOT, args.base_config)
    base = json.load(open(bp)) if os.path.exists(bp) else {}

    variants = {
        "sheet  (his formula, as-is)": dict(base, rater="grades", grade_formula="sheet",
                                            scale=1.0, hfa=base.get("hfa", 3.0)),
        "fixed  (3 corrections)":      dict(base, rater="grades", grade_formula="fixed",
                                            grade_scale=1.0, quality_scale=1.0,
                                            scale=1.0, hfa=base.get("hfa", 3.0)),
        "elo    (results only)":       dict(base, rater="elo"),
        "blend  (elo + grades)":       dict(base, rater="grades", grade_formula="fixed",
                                            grade_scale=1.0, quality_scale=1.0,
                                            scale=1.0),
    }
    # Build the blend properly through BlendRater rather than by hand.
    variants["blend  (elo + grades)"] = dict(
        base, rater="blend", grade_formula="fixed", grade_scale=1.0,
        quality_scale=1.0, blend_weight=0.5)

    eligible, skipped = eligible_game_ids(games, grades, args.season, weeks)
    print("%s %d, weeks %s — all variants scored on identical games"
          % (args.sport.upper(), args.season, args.weeks))
    print("%d games where BOTH teams are graded; %d excluded (ungraded opponent)\n"
          % (len(eligible), skipped))
    hdr = "%-28s %6s %8s %9s %8s %9s %8s" % (
        "variant", "n", "ATS%", "ROI%", "MAE", "slope", "SU%")
    print(hdr); print("-" * len(hdr))

    results = {}
    for name, cfg in variants.items():
        preds = run_variant(games, cfg, args.season, weeks, grades, eligible)
        m = metrics.evaluate(preds)
        results[name] = m
        print("%-28s %6s %8s %9s %8s %9s %8s" % (
            name, m.get("ats_n", 0),
            "%.2f" % m["ats_pct"] if m.get("ats_pct") is not None else "-",
            "%+.2f" % m["roi"] if m.get("roi") is not None else "-",
            "%.2f" % m["mae"] if m.get("mae") is not None else "-",
            "%.3f" % m["calib_slope"] if m.get("calib_slope") is not None else "-",
            "%.2f" % m["su_pct"] if m.get("su_pct") is not None else "-"))

    base_su = next((m.get("su_baseline_pct") for m in results.values()
                    if m.get("su_baseline_pct")), None)
    print("\n  break-even ATS 52.38%%   |   market-favorite SU %s"
          % ("%.2f%%" % base_su if base_su else "n/a"))

    sheet = results["sheet  (his formula, as-is)"]
    if sheet.get("calib_slope"):
        print("\n  Calibration of the sheet as it stands: slope %.3f" % sheet["calib_slope"])
        print("  -> multiply its spreads by %.2f to make them honest."
              % sheet["calib_slope"])
    print("\n  Reminder: one partial season is a SMALL sample. Treat every gap")
    print("  under ~4 points of ATS as noise until more seasons are imported.")


if __name__ == "__main__":
    main()
