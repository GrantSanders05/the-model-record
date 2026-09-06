"""
metrics.py — honest evaluation of a set of predictions.

Design principle: make it hard to fool yourself.

Grant's stated goal is to run "a butt load" of tests to find the best formula.
That is exactly right, and it is also exactly how people end up shipping noise.
Search hard enough over any fixed history and something will hit 60% ATS by
luck alone. So every headline number here ships with the context needed to
know whether it is real:

  * ATS% is reported against BOTH the 50% coin-flip null (is there signal?)
    and the 52.38% break-even (is it profitable?).
  * A z-score and 95% confidence interval accompany the win rate, because
    58% on 120 games and 54% on 4,000 games are not the same claim.
  * Straight-up accuracy is always shown beside the "pick the favorite"
    baseline, because SU% on its own is a vanity metric.
  * Calibration slope is reported because a model can pick winners well and
    still lose money by systematically overstating margins -- which is
    precisely the flaw measured in the current model (slope 0.62).

Pure stdlib.
"""

import math

BREAK_EVEN = 0.5238095238095238   # -110 juice: 110/210
WIN_UNITS = 100.0 / 110.0         # profit on a 1-unit win at -110


# ── small stats helpers ────────────────────────────────────────────────────────

def _norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def binom_z(wins, n, p0=0.5):
    """Z-score of an observed win rate against a null rate."""
    if n == 0:
        return 0.0
    se = math.sqrt(p0 * (1 - p0) / n)
    return ((wins / n) - p0) / se if se else 0.0


def wilson_ci(wins, n, z=1.96):
    """Wilson score interval — behaves sensibly at small n, unlike normal approx."""
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def linreg(xs, ys):
    """Return (slope, intercept, r). Used for calibration checks."""
    n = len(xs)
    if n < 3:
        return (None, None, None)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0 or syy == 0:
        return (None, None, None)
    slope = sxy / sxx
    return (slope, my - slope * mx, sxy / math.sqrt(sxx * syy))


# ── core evaluation ────────────────────────────────────────────────────────────

def evaluate(preds, edge_threshold=0.0):
    """
    Score a list of predictions.

    Each pred is a dict with at minimum:
        pred_margin   -- model's predicted HOME margin
        market_margin -- closing line as HOME margin (None = no line, skipped for ATS)
        actual_margin -- final HOME margin

    Optional:
        pred_total / market_total / actual_total  -- enables totals scoring

    `edge_threshold` bets only games where |pred - market| >= threshold.
    A model with a real edge should get BETTER as this rises. If it gets worse,
    the model's disagreements with the market are noise, not insight -- which
    is one of the most useful diagnostics available.
    """
    graded = [p for p in preds if p.get("actual_margin") is not None]
    out = {"n_games": len(graded)}
    if not graded:
        return out

    # ── straight up ──
    su_correct = sum(
        1 for p in graded
        if p["pred_margin"] != 0 and p["actual_margin"] != 0
        and (p["pred_margin"] > 0) == (p["actual_margin"] > 0)
    )
    su_n = sum(1 for p in graded if p["pred_margin"] != 0 and p["actual_margin"] != 0)
    out["su_n"] = su_n
    out["su_pct"] = 100.0 * su_correct / su_n if su_n else None

    # Baseline: what would picking the market favorite have got?
    withline = [p for p in graded if p.get("market_margin") is not None]
    fav_n = sum(1 for p in withline if p["market_margin"] != 0 and p["actual_margin"] != 0)
    fav_correct = sum(
        1 for p in withline
        if p["market_margin"] != 0 and p["actual_margin"] != 0
        and (p["market_margin"] > 0) == (p["actual_margin"] > 0)
    )
    out["su_baseline_pct"] = 100.0 * fav_correct / fav_n if fav_n else None
    out["su_edge_vs_baseline"] = (
        out["su_pct"] - out["su_baseline_pct"]
        if out["su_pct"] is not None and out["su_baseline_pct"] is not None else None
    )

    # ── accuracy of the margin itself ──
    err = [p["pred_margin"] - p["actual_margin"] for p in graded]
    out["mae"] = sum(abs(e) for e in err) / len(err)
    out["rmse"] = math.sqrt(sum(e * e for e in err) / len(err))
    out["bias"] = sum(err) / len(err)     # + means model favors home too much

    slope, icpt, r = linreg([p["pred_margin"] for p in graded],
                            [p["actual_margin"] for p in graded])
    out["calib_slope"], out["calib_intercept"], out["calib_r"] = slope, icpt, r
    out["calib_r2"] = r * r if r is not None else None
    # The multiplier that WOULD have made this model calibrated.
    out["suggested_scale"] = slope if slope else None

    # Null model: how good is "always predict the average margin"?
    ys = [p["actual_margin"] for p in graded]
    my = sum(ys) / len(ys)
    out["null_rmse"] = math.sqrt(sum((y - my) ** 2 for y in ys) / len(ys))

    # ── against the spread ──
    bets = [p for p in withline if abs(p["pred_margin"] - p["market_margin"]) >= edge_threshold]
    w = l = push = 0
    for p in bets:
        edge = p["pred_margin"] - p["market_margin"]
        if edge == 0:
            continue
        diff = p["actual_margin"] - p["market_margin"]   # + = home covered
        if diff == 0:
            push += 1
        elif (edge > 0) == (diff > 0):
            w += 1
        else:
            l += 1

    out["ats_w"], out["ats_l"], out["ats_push"] = w, l, push
    decided = w + l
    out["ats_n"] = decided
    if decided:
        out["ats_pct"] = 100.0 * w / decided
        out["ats_z_vs_coinflip"] = binom_z(w, decided, 0.5)
        out["ats_p_vs_coinflip"] = 1 - _norm_cdf(out["ats_z_vs_coinflip"])
        lo, hi = wilson_ci(w, decided)
        out["ats_ci95"] = (100 * lo, 100 * hi)
        out["ats_beats_breakeven"] = out["ats_pct"] > 100 * BREAK_EVEN
        # Is the whole 95% CI above break-even? The only honest "this is real".
        out["ats_significant"] = 100 * lo > 100 * BREAK_EVEN
        out["roi"] = 100.0 * (w * WIN_UNITS - l) / decided
    else:
        out["ats_pct"] = None
        out["roi"] = None
        out["ats_significant"] = False

    # ── how far the model sits from the market ──
    #
    # THIS IS NOT CLOSING LINE VALUE, and it was called `clv_mean` with a comment
    # above it describing CLV. It is the mean ABSOLUTE gap between the model and
    # the market at prediction time: a measure of how much the model disagrees,
    # with no direction and no second observation in it.
    #
    # Real CLV needs two market observations -- one at the wager and one at the
    # close -- and a side, so that "we got the better number" has a sign. That
    # lives in grading.line_clv and, for actual wagers, in bet_log. A number that
    # rises whenever the model shouts louder is not a leading indicator of edge;
    # under-dispersion would make it fall while the model got better.
    out["mean_abs_market_disagreement"] = (
        sum(abs(p["pred_margin"] - p["market_margin"]) for p in withline)
        / len(withline)) if withline else None
    # DEPRECATED alias, kept so nothing reading the old key breaks mid-transition.
    # Nothing in the export or publish path reads it; remove it once the backtest
    # JSON consumers have moved.
    out["clv_mean"] = out["mean_abs_market_disagreement"]

    # ── totals (only if supplied) ──
    tot = [p for p in graded
           if p.get("pred_total") is not None and p.get("actual_total") is not None]
    if tot:
        terr = [p["pred_total"] - p["actual_total"] for p in tot]
        out["total_mae"] = sum(abs(e) for e in terr) / len(terr)
        out["total_rmse"] = math.sqrt(sum(e * e for e in terr) / len(terr))
        ts, ti, tr = linreg([p["pred_total"] for p in tot],
                            [p["actual_total"] for p in tot])
        out["total_calib_slope"], out["total_calib_r"] = ts, tr
        ou = [p for p in tot if p.get("market_total") is not None]
        tw = tl = tp = 0
        for p in ou:
            edge = p["pred_total"] - p["market_total"]
            diff = p["actual_total"] - p["market_total"]
            if edge == 0:
                continue
            if diff == 0:
                tp += 1
            elif (edge > 0) == (diff > 0):
                tw += 1
            else:
                tl += 1
        out["total_w"], out["total_l"], out["total_push"] = tw, tl, tp
        if tw + tl:
            out["total_pct"] = 100.0 * tw / (tw + tl)
            lo, hi = wilson_ci(tw, tw + tl)
            out["total_ci95"] = (100 * lo, 100 * hi)
            out["total_significant"] = 100 * lo > 100 * BREAK_EVEN
            out["total_roi"] = 100.0 * (tw * WIN_UNITS - tl) / (tw + tl)
    return out


def edge_buckets(preds, bounds=(0, 1, 3, 6, 10, 100)):
    """
    ATS performance sliced by how far the model disagrees with the market.

    A model with genuine insight earns MORE as disagreement grows. A model that
    is merely miscalibrated looks fine on small disagreements and bleeds on
    large ones. This slice tells the two apart.
    """
    rows = []
    for lo, hi in zip(bounds, bounds[1:]):
        sel = [p for p in preds
               if p.get("market_margin") is not None
               and p.get("actual_margin") is not None
               and lo <= abs(p["pred_margin"] - p["market_margin"]) < hi]
        if not sel:
            continue
        m = evaluate(sel)
        rows.append({
            "range": "%g-%g" % (lo, hi),
            "n": m.get("ats_n", 0),
            "ats_pct": m.get("ats_pct"),
            "roi": m.get("roi"),
        })
    return rows


def format_report(m, title="results"):
    """Readable summary with the caveats attached to the numbers."""
    L = []
    L.append("── %s ──" % title)
    L.append("  games                : %d" % m.get("n_games", 0))
    if m.get("su_pct") is not None:
        base = m.get("su_baseline_pct")
        L.append("  straight-up          : %.2f%%   (market favorite: %s)"
                 % (m["su_pct"], ("%.2f%%" % base) if base is not None else "n/a"))
        if m.get("su_edge_vs_baseline") is not None:
            L.append("     -> vs baseline    : %+.2f pts %s"
                     % (m["su_edge_vs_baseline"],
                        "" if m["su_edge_vs_baseline"] > 0 else "(WORSE than just taking the favorite)"))
    L.append("  MAE / RMSE           : %.2f / %.2f   (null RMSE %.2f)"
             % (m.get("mae", 0), m.get("rmse", 0), m.get("null_rmse", 0)))
    if m.get("calib_slope") is not None:
        L.append("  calibration slope    : %.3f   (1.000 = correctly scaled)" % m["calib_slope"])
        L.append("  calibration r2       : %.3f" % (m.get("calib_r2") or 0))
        if abs(m["calib_slope"] - 1.0) > 0.08:
            L.append("     -> rescale predictions by x%.3f to calibrate" % m["calib_slope"])
    if m.get("ats_pct") is not None:
        lo, hi = m["ats_ci95"]
        L.append("  ATS                  : %.2f%%  (%d-%d-%d)"
                 % (m["ats_pct"], m["ats_w"], m["ats_l"], m["ats_push"]))
        L.append("     95%% CI            : %.2f%% - %.2f%%" % (lo, hi))
        L.append("     break-even        : 52.38%")
        L.append("     ROI               : %+.2f%% per unit" % m["roi"])
        verdict = ("PROVEN edge (entire CI above break-even)" if m.get("ats_significant")
                   else "above break-even but NOT statistically proven — could be noise"
                   if m.get("ats_beats_breakeven")
                   else "below break-even — not profitable")
        L.append("     verdict           : %s" % verdict)
    if m.get("total_pct") is not None:
        lo, hi = m["total_ci95"]
        L.append("  totals O/U           : %.2f%%  (%d-%d-%d)  ROI %+.2f%%"
                 % (m["total_pct"], m["total_w"], m["total_l"], m["total_push"], m["total_roi"]))
        L.append("     95%% CI            : %.2f%% - %.2f%%   MAE %.2f pts"
                 % (lo, hi, m.get("total_mae", 0)))
    return "\n".join(L)
