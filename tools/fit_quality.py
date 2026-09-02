"""
fit_quality.py — choose the win/loss quality rule by measurement, not memory.

WHAT THIS IS FOR
The four spreadsheet columns Wins / Losses / Win Points / Loss Points were
filled in BY HAND. For 2026 they are empty on all 138 teams, so the entire
quality term contributes zero and the ratings are bare position grades.

`GradeRater.observe()` has meanwhile been accruing the same quantity from real
results on every run, and nothing has ever read it. `grade_formula: computed`
wires it in. This picks the seven numbers that rule uses.

WHY IT IS NOT JUST A GRID SEARCH ON 2025
934 games is small enough that a five-parameter search will find something that
looks good and is noise. So the rule is FIT on the first half of the season and
SCORED on the second, which it has never seen. A rule that only works in-sample
is reported as such and not recommended.

Three baselines are printed for context, because "55%" means nothing alone:
  none   quality term off entirely -- what 2026 is doing TODAY
  sheet  Grant's hand-entered points -- the historical benchmark, and NOT
         available going forward, since nobody is filling the columns in
  fitted what this search chose

    python3 tools/fit_quality.py                # report only, changes nothing
    python3 tools/fit_quality.py --apply        # write it into the config
"""

import argparse
import itertools
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import backtest      # noqa: E402
import db            # noqa: E402
import metrics       # noqa: E402

CONFIG = os.path.join(ROOT, "config", "cfb_grades.json")
SEASON = 2025
BREAK_EVEN = 52.38               # -110 juice; the only bar that means anything
FIT_WEEKS = range(1, 9)          # weeks 1-8 choose the rule
HOLD_WEEKS = range(9, 20)        # weeks 9+ judge it, unseen

# THE SEARCH SPACE HAD A HOLE IN IT, AND THE HOLE WAS THE ANSWER.
# The first version pinned lq_ranked at 0.0 -- inherited from the config, where
# "losing to a ranked team costs nothing" had never been questioned. Regressing
# Grant's OWN hand-entered numbers on computable features says otherwise: he
# charged about -1.45 for a loss to a ranked team (R^2 0.84 on Loss Points, 0.73
# on Win Points). So the grid could not express the rule that actually worked,
# and unsurprisingly it found nothing that did.
#
# Ranges are centred on those regressed coefficients rather than on the config's
# round numbers, which were roughly double his throughout:
#     his implied rule   top5 +2.4  top10 +1.0  top25 +0.6
#                        FCS loss -3.1  unranked -1.9  ranked -1.45
#     the old config     top5 +4    top10 +3    top25 +2
#                        FCS loss -4    unranked -2    ranked  0
SPACE = {
    # RANGES COME FROM REGRESSING GRANT'S OWN HAND-ENTERED NUMBERS on computable
    # bucket counts, not from the config's round numbers. That regression fits
    # his Loss Points at R^2 0.86 and his Win Points at 0.62, and says his rule
    # was roughly DOUBLE the config's and charged for ranked losses, which the
    # config pinned at zero:
    #
    #     implied   top5 +0.5  top10 +1.9  top25 +1.0
    #               FCS loss -9.2  unranked -3.9  ranked -1.7
    #     config    top5 +4    top10 +3    top25 +2
    #               FCS loss -4    unranked -2    ranked  0
    #
    # A NOTE ON SIGNS, because I got this wrong once and it cost an hour. Loss
    # Points are stored POSITIVE in the weekly tabs and NEGATIVE in Team Data,
    # and the engine negates them. Reading Team Data made the term look inverted
    # (bad teams gaining rating); reading what the backtest actually loads shows
    # it correlates +0.729 with win rate, which is the right direction. The lq_*
    # values here are what is ADDED to the loser, so they are negative.
    "wq_top5":         [0.0, 0.5, 2.0],
    "wq_top10":        [1.0, 2.0, 3.0],
    "wq_top25":        [0.5, 1.0, 2.0],
    "wq_other":        [0.0, 0.25],
    "lq_ranked":       [0.0, -1.0, -1.7, -2.5],
    "lq_unranked_fbs": [-2.0, -3.0, -3.9, -5.0],
    "lq_fcs":          [-4.0, -6.0, -9.2, -12.0],
}


def split(preds, weeks):
    return [p for p in preds if p.get("week") in weeks]


def score(preds):
    """ATS% and how far above break-even it is, on a given slice."""
    if not preds:
        return None
    m = metrics.evaluate(preds)
    if not m.get("n_games") or m.get("ats_pct") is None:
        return None
    return m


def run(games, grades, base, formula, extra=None):
    cfg = dict(base)
    cfg["grade_formula"] = formula
    if extra:
        cfg.update(extra)
    cfg["_grades"] = grades
    return backtest.run(games, cfg, test_seasons=[SEASON])


def line(label, m):
    if not m:
        return "%-34s (no games)" % label
    return ("%-34s n=%-4d ATS %6.2f%%  MAE %5.2f  ROI %6.2f%%"
            % (label, m["n_games"], m["ats_pct"], m["mae"], m.get("roi") or 0.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the fitted rule into config/cfb_grades.json")
    args = ap.parse_args()

    conn = db.connect()
    games = backtest.load_games(conn, "cfb")
    grades = backtest.load_grades(conn, "cfb")
    base = json.load(open(CONFIG))
    if not grades:
        sys.exit("no grades loaded — cannot fit the quality rule")

    zero = {k: 0.0 for k in SPACE}
    baselines = {
        "none  (what 2026 does today)": run(games, grades, base, "computed", zero),
        "sheet (hand-entered points)":  run(games, grades, base, "sheet"),
        # HIS OWN RULE, WRITTEN IN THE SPREADSHEET HEADER. Both tabs document it
        # in a note beside the columns, and the two notes DISAGREE -- every
        # weekly snapshot says +5/+4/+3 with a -4 unranked loss, while the live
        # Team Data tab says +4/+3/+2 with -2. The config had been carrying the
        # Team Data numbers, which are the weaker of the two by 1.6 points of
        # ATS. The weekly note is the rule he actually applied for the season
        # those grades were entered in, and it is the one to beat.
        "weekly note (+5/+4/+3, -4/-4)": run(games, grades, base, "computed", dict(
            wq_top5=5.0, wq_top10=4.0, wq_top25=3.0, wq_other=0.0,
            lq_ranked=0.0, lq_unranked_fbs=-4.0, lq_fcs=-4.0)),
        "Team Data note (+4/+3/+2, -2/-4)": run(games, grades, base, "computed", dict(
            wq_top5=4.0, wq_top10=3.0, wq_top25=2.0, wq_other=0.0,
            lq_ranked=0.0, lq_unranked_fbs=-2.0, lq_fcs=-4.0)),
    }

    print("BASELINES, whole of %d" % SEASON)
    for k, preds in baselines.items():
        print("  " + line(k, score(preds)))

    keys = sorted(SPACE)
    combos = list(itertools.product(*(SPACE[k] for k in keys)))
    print("\nsearching %d combinations on weeks 1-8, scoring on weeks 9+..."
          % len(combos))

    best = None
    for values in combos:
        extra = dict(zip(keys, values))
        preds = run(games, grades, base, "computed", extra)
        fit = score(split(preds, FIT_WEEKS))
        if not fit:
            continue
        # Fit on ATS, because that is the thing being bet. MAE is reported so a
        # rule that wins ATS by making the point predictions worse is visible.
        key = (-fit["ats_pct"], fit["mae"])
        if best is None or key < best[0]:
            best = (key, extra, preds)

    if best is None:
        sys.exit("no combination produced a scorable fit window")

    _, chosen, preds = best
    print("\nCHOSEN RULE")
    for k in keys:
        print("  %-18s %+.1f" % (k, chosen[k]))

    print("\nHOW IT SCORES")
    print("  " + line("fitted — weeks 1-8 (in-sample)", score(split(preds, FIT_WEEKS))))
    print("  " + line("fitted — weeks 9+ (HELD OUT)", score(split(preds, HOLD_WEEKS))))
    print("  " + line("fitted — whole season", score(preds)))
    for k, bp in baselines.items():
        print("  " + line(k + " — weeks 9+", score(split(bp, HOLD_WEEKS))))

    held = score(split(preds, HOLD_WEEKS))
    none_held = score(split(baselines["none  (what 2026 does today)"], HOLD_WEEKS))
    sheet_held = score(split(baselines["sheet (hand-entered points)"], HOLD_WEEKS))

    # BEATING NOTHING IS NOT THE BAR, and the first version of this gate said it
    # was. It passed a rule that scored 50.86% held out with -2.91% ROI, purely
    # because the no-quality baseline was worse. A rule that loses money slightly
    # more slowly is not a rule worth shipping. The bar everywhere else in this
    # project is BREAK-EVEN at -110, and it is the bar here too.
    beats_none = bool(held and none_held and held["ats_pct"] > none_held["ats_pct"])
    clears_be = bool(held and held["ats_pct"] > BREAK_EVEN)
    verdict_ok = beats_none and clears_be

    print("\nVERDICT (weeks 9+, never seen by the fit)")
    print("  fitted %.2f%%  vs  no-quality %.2f%%   -> %s"
          % (held["ats_pct"] if held else 0, none_held["ats_pct"] if none_held else 0,
             "better" if beats_none else "NOT better"))
    print("  fitted %.2f%%  vs  break-even %.2f%%  -> %s"
          % (held["ats_pct"] if held else 0, BREAK_EVEN,
             "clears" if clears_be else "DOES NOT CLEAR"))
    if sheet_held:
        print("  for scale, the hand-entered points scored %.2f%% on the same games."
              % sheet_held["ats_pct"])
    note_held = score(split(baselines["weekly note (+5/+4/+3, -4/-4)"], HOLD_WEEKS))
    if note_held:
        print("  his own documented weekly rule scored %.2f%% -- beat THAT before "
              "preferring a searched one." % note_held["ats_pct"])
    if not verdict_ok:
        print("\n  NOT SHIPPABLE. %s"
              % ("The search found noise." if not beats_none else
                 "It beats an empty term but still does not make money."))

    if args.apply:
        if not verdict_ok:
            sys.exit("refusing to --apply a rule that does not clear break-even "
                     "on games it never saw")
        cfg = json.load(open(CONFIG))
        cfg.update(chosen)
        cfg["grade_formula"] = "computed"
        with open(CONFIG, "w") as fh:
            json.dump(cfg, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print("\nwritten to %s (grade_formula -> computed)" % CONFIG)
    else:
        print("\n(report only — pass --apply to write it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
