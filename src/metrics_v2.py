"""
metrics_v2.py — four scoreboards, and never one number for all of them.

"The model went X-Y" was ambiguous between every forecast, every lined game,
every game inside the +/-28 band, every fully graded game, and the actual board.
These are the four questions, kept apart on purpose:

    A. FORECAST QUALITY   every forecast, bet or not. How accurate is the model?
    B. SIGNAL PERFORMANCE only official signals, at the line they locked, priced
                          honestly. What did the strategy do?
    C. CLOSING DIAGNOSTIC the same side at the close. Did it beat the market?
                          A DIAGNOSTIC. Not a wager anybody made.
    D. USER BETS          what a person actually wagered. Not modelled here.

They answer different questions and they have different denominators. A is over
every game the model priced; B is over the far smaller set the strategy offered.
Putting them under one heading is how a record becomes unfalsifiable.

THE BASELINE HAS TO BE AT THE SAME TIME. Comparing a T2 forecast's error with
the CLOSING line's error flatters the model or damns it depending on which way
the market moved, and says nothing either way. The market's number at the same
instant is the only fair comparison, and it is stored on the feature snapshot for
exactly this reason.
"""

import json
import math
import random
import statistics


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _wilson(w, n, z=1.96):
    """Wilson interval, which behaves at small n where the normal one does not."""
    if not n:
        return (None, None)
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(100 * (c - m) / d, 1), round(100 * (c + m) / d, 1))


# §19.4. Games inside one weekend share the market, the news cycle and the
# weather. Resampling GAMES treats forty correlated observations as forty
# independent ones and reports an interval that is too narrow — flattering,
# stable-looking, and wrong in the direction that gets a challenger promoted.
# Resampling WEEKS keeps the correlation inside the unit being resampled.
BOOTSTRAP_ITERS = 2000
BOOTSTRAP_SEED = 20260906
MIN_BOOTSTRAP_WEEKS = 4


def block_bootstrap_ci(by_week, *, iters=BOOTSTRAP_ITERS, seed=BOOTSTRAP_SEED,
                       alpha=0.05):
    """
    A percentile CI for the mean, resampling whole weeks. -> dict

    Fixed seed, so the same data gives the same interval and a number quoted in a
    report can be reproduced. Returns a `why` instead of an interval when there
    are too few weeks: an interval over three weekends is a statement about three
    weekends, and printing it with a 95% label would be the fake precision §27.3
    forbids.
    """
    weeks = [w for w, vals in by_week.items() if vals]
    out = {"weeks": len(weeks), "iters": iters, "seed": seed,
           "lo": None, "hi": None}
    if len(weeks) < MIN_BOOTSTRAP_WEEKS:
        out["why"] = ("%d week(s) of evidence; a block bootstrap needs at least %d "
                      "before its interval means anything"
                      % (len(weeks), MIN_BOOTSTRAP_WEEKS))
        return out
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        pooled = []
        for _ in range(len(weeks)):
            pooled.extend(by_week[weeks[rng.randrange(len(weeks))]])
        if pooled:
            means.append(sum(pooled) / len(pooled))
    if not means:
        out["why"] = "no resample produced an observation"
        return out
    means.sort()
    lo = means[int((alpha / 2) * (len(means) - 1))]
    hi = means[int((1 - alpha / 2) * (len(means) - 1))]
    out["lo"] = round(lo, 3)
    out["hi"] = round(hi, 3)
    # Said explicitly, because "the interval contains zero" is the sentence that
    # keeps getting dropped between a table and a decision.
    out["separated_from_zero"] = bool(lo > 0 or hi < 0)
    return out


def forecast_quality(conn, *, model_version=None, horizon=None, sport="cfb",
                     season=None):
    """
    A. How accurate is the model, over EVERY forecast it made?

    Paired against the market number the model itself saw, from the same feature
    snapshot. `paired_delta` is the model's absolute error minus the market's, so
    negative means the model was closer.
    """
    q = ("SELECT f.*, s.payload_json, g.home_score, g.away_score, g.season"
         "  FROM forecast_log f"
         "  JOIN feature_snapshots s ON s.feature_snapshot_id = f.feature_snapshot_id"
         "  JOIN games g ON g.game_id = f.game_id"
         " WHERE g.sport=? AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL")
    args = [sport]
    if model_version:
        q += " AND f.model_version=?"
        args.append(model_version)
    if horizon:
        q += " AND f.horizon=?"
        args.append(horizon)
    if season:
        q += " AND g.season=?"
        args.append(season)

    rows = []
    for r in conn.execute(q, args):
        payload = json.loads(r["payload_json"])
        actual = r["home_score"] - r["away_score"]
        pred = r["pred_home_margin"]
        mkt = payload.get("consensus_spread")
        if pred is None:
            continue
        rows.append({"pred": pred, "actual": actual, "market": mkt,
                     "err": abs(actual - pred),
                     "mkt_err": (abs(actual - mkt) if mkt is not None else None)})

    out = {"n": len(rows), "model_version": model_version, "horizon": horizon}
    # Attached BEFORE the early return. A margin scoreboard with nothing in it and
    # a probability scoreboard with nothing in it are separate facts, and hanging
    # the second off the first meant the panel vanished whenever the first was
    # empty — which is exactly when a reader most wants to know why.
    out["probability"] = probability_quality(
        conn, model_version=model_version, horizon=horizon, sport=sport,
        season=season) if model_version else None
    if not rows:
        return out
    out["mae"] = round(_mean([r["err"] for r in rows]), 3)
    out["rmse"] = round(math.sqrt(_mean([(r["actual"] - r["pred"]) ** 2 for r in rows])), 3)
    out["bias"] = round(_mean([r["pred"] - r["actual"] for r in rows]), 3)

    paired = [r for r in rows if r["mkt_err"] is not None]
    out["paired_n"] = len(paired)
    if paired:
        out["market_mae"] = round(_mean([r["mkt_err"] for r in paired]), 3)
        deltas = [r["err"] - r["mkt_err"] for r in paired]
        out["paired_delta"] = round(_mean(deltas), 3)
        if len(deltas) > 1:
            se = statistics.stdev(deltas) / math.sqrt(len(deltas))
            out["paired_se"] = round(se, 3)
            out["paired_t"] = round(out["paired_delta"] / se, 2) if se else None
        out["beat_market_pct"] = round(
            100.0 * sum(1 for d in deltas if d < 0) / len(deltas), 1)

    if len(rows) > 2:
        xs = [r["pred"] for r in rows]
        ys = [r["actual"] for r in rows]
        mx, my = _mean(xs), _mean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        out["calibration_slope"] = round(
            sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den, 3) if den else None
    return out


# The floor and ceiling a probability may be scored at. A model that emits a
# bare 0 or 1 has claimed certainty about a football game; log loss would be
# infinite and one row would swamp the season. Clipping is standard, and the
# COUNT of clipped rows is reported, because a model doing it often is telling
# you something about itself.
PROB_CLIP = 1e-6
CALIBRATION_BINS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))


def probability_quality(conn, *, model_version, horizon=None, sport="cfb",
                        season=None):
    """
    Is the win probability any good, and is it any better than the price? -> dict

    §12.5 keeps moneylines off until a prospectively calibrated probability
    exists. This is the measurement that would eventually answer that, and it is
    deliberately paired against the DE-VIGGED market probability from the same
    feature snapshot — §19.2's same-time baseline. Comparing a T2 model
    probability against a closing price would flatter the model for being late.

    Nothing here promotes anything. `enables_moneyline` is False with a reason,
    and it stays False until a human reads a prospective sample.
    """
    q = ("SELECT f.home_win_prob, f.horizon, s.payload_json,"
         "       g.home_score, g.away_score, g.week"
         "  FROM forecast_log f"
         "  JOIN feature_snapshots s ON s.feature_snapshot_id = f.feature_snapshot_id"
         "  JOIN games g ON g.game_id = f.game_id"
         " WHERE g.sport=? AND f.model_version=? AND f.home_win_prob IS NOT NULL"
         "   AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL")
    args = [sport, model_version]
    if horizon:
        q += " AND f.horizon=?"
        args.append(horizon)
    if season:
        q += " AND g.season=?"
        args.append(season)

    rows, clipped = [], 0
    for r in conn.execute(q, args):
        if r["home_score"] == r["away_score"]:
            # Not scored rather than scored as a loss. A tie is not a home loss,
            # and college football does not produce them — so one appearing is a
            # data problem, and silently grading it would hide that.
            continue
        p = float(r["home_win_prob"])
        if p <= PROB_CLIP or p >= 1 - PROB_CLIP:
            clipped += 1
            p = min(max(p, PROB_CLIP), 1 - PROB_CLIP)
        payload = json.loads(r["payload_json"])
        rows.append({"p": p, "y": 1 if r["home_score"] > r["away_score"] else 0,
                     "market": payload.get("consensus_home_prob"),
                     "week": r["week"]})

    out = {"model_version": model_version, "horizon": horizon, "n": len(rows),
           "clipped": clipped,
           "enables_moneyline": False,
           "why": "a probability is not usable as a price until it has a "
                  "prospective calibration sample a human has read; §12.5 keeps "
                  "moneyline_enabled False until then"}
    if not rows:
        return out
    out["brier"] = round(_mean([(r["p"] - r["y"]) ** 2 for r in rows]), 4)
    out["log_loss"] = round(_mean([
        -(r["y"] * math.log(r["p"]) + (1 - r["y"]) * math.log(1 - r["p"]))
        for r in rows]), 4)
    out["base_rate"] = round(_mean([r["y"] for r in rows]), 4)
    # The reference every Brier score needs: what a model that always predicted
    # the base rate would have scored. A Brier of 0.24 means nothing on its own.
    out["brier_base_rate_model"] = round(
        _mean([(out["base_rate"] - r["y"]) ** 2 for r in rows]), 4)

    paired = [r for r in rows if r["market"] is not None]
    out["paired_n"] = len(paired)
    if paired:
        mkt = _mean([(r["market"] - r["y"]) ** 2 for r in paired])
        own = _mean([(r["p"] - r["y"]) ** 2 for r in paired])
        out["market_brier"] = round(mkt, 4)
        out["brier_vs_market"] = round(own - mkt, 4)
        by_week = {}
        for r in paired:
            by_week.setdefault(r["week"], []).append(
                (r["market"] - r["y"]) ** 2 - (r["p"] - r["y"]) ** 2)
        out["block_bootstrap"] = block_bootstrap_ci(by_week)

    bins = []
    for lo, hi in CALIBRATION_BINS:
        inside = [r for r in rows if lo <= r["p"] < hi or (hi == 1.0 and r["p"] == 1.0)]
        bins.append({"lo": lo, "hi": hi, "n": len(inside),
                     "predicted": round(_mean([r["p"] for r in inside]), 4) if inside else None,
                     "realized": round(_mean([r["y"] for r in inside]), 4) if inside else None})
    out["calibration"] = bins
    return out


def signal_performance(conn, *, strategy_version, sport="cfb", market=None):
    """
    B. What the strategy actually did, at the numbers it locked.

    ROI is over signals whose price is KNOWN. Where no price was recorded there
    is no return to state, and `roi` is None rather than a figure computed from
    an assumed -110.

    `market` narrows to one bet type. A spread and a total are bets on different
    quantities and pooling them is not a record of either.
    """
    import signals as sig
    rec = sig.official_record(conn, strategy_version=strategy_version, sport=sport,
                              market=market)
    n = (rec.get("locked_w") or 0) + (rec.get("locked_l") or 0)
    rec["locked_ci95"] = _wilson(rec.get("locked_w") or 0, n)
    rec["break_even"] = 52.38
    rec["roi_basis"] = ("exact recorded prices" if rec.get("priced_n")
                        else "no prices were recorded, so there is no ROI")
    return rec


def closing_diagnostic(conn, *, strategy_version, sport="cfb", market=None):
    """
    C. The same side, at the close. A DIAGNOSTIC and not a wager record.

    Beating the close is the leading indicator most worth watching, and it is not
    money. Labelled here so it cannot be quoted as the strategy's return.

    `market` narrows it, for the same reason it narrows the record above: a
    diagnostic printed beside a spread record has to be about spreads.
    """
    import signals as sig
    rec = sig.official_record(conn, strategy_version=strategy_version, sport=sport,
                              market=market)
    n = (rec.get("close_w") or 0) + (rec.get("close_l") or 0)
    return {
        "strategy_version": strategy_version, "market": market or "all",
        "n": n, "w": rec.get("close_w"), "l": rec.get("close_l"),
        "p": rec.get("close_p"), "pct": rec.get("close_pct"),
        "ci95": _wilson(rec.get("close_w") or 0, n),
        "clv_mean": rec.get("clv_mean"), "clv_n": rec.get("clv_n"),
        "clv_beat_pct": rec.get("clv_beat_pct"),
        "is_diagnostic": True,
        "note": "the side the strategy took, scored at the closing number. Not a "
                "wager: nothing was placed at this line.",
    }


def paired_comparison(conn, *, champion_version, challenger_version, horizon=None,
                      sport="cfb"):
    """
    Champion against a challenger, ON THE SAME GAMES.

    Paired, because comparing two whole-season averages over different game sets
    compares the game sets. Returns the mean paired improvement in absolute
    error, its standard error, and a win/tie/loss count.
    """
    q = ("SELECT f.model_version, f.game_id, f.pred_home_margin,"
         "       g.home_score, g.away_score, g.week"
         "  FROM forecast_log f JOIN games g ON g.game_id=f.game_id"
         " WHERE g.sport=? AND f.model_version IN (?,?)"
         "   AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL")
    args = [sport, champion_version, challenger_version]
    if horizon:
        q += " AND f.horizon=?"
        args.append(horizon)
    by_game, week_of = {}, {}
    for r in conn.execute(q, args):
        actual = r["home_score"] - r["away_score"]
        if r["pred_home_margin"] is None:
            continue
        week_of[r["game_id"]] = r["week"]
        by_game.setdefault(r["game_id"], {})[r["model_version"]] = abs(
            actual - r["pred_home_margin"])
    paired_games = [gid for gid, v in by_game.items()
                    if champion_version in v and challenger_version in v]
    both = [(by_game[g][champion_version], by_game[g][challenger_version])
            for g in paired_games]
    by_week = {}
    for gid in paired_games:
        by_week.setdefault(week_of.get(gid), []).append(
            by_game[gid][champion_version] - by_game[gid][challenger_version])
    out = {"champion": champion_version, "challenger": challenger_version,
           "paired_n": len(both), "horizon": horizon,
           # Always present, even at zero pairs. An absent key and an interval
           # that cannot be computed yet look identical to a reader, and only one
           # of them is a bug.
           "block_bootstrap": block_bootstrap_ci(by_week)}
    if not both:
        return out
    deltas = [c - ch for c, ch in both]        # positive = challenger closer
    out["mean_improvement"] = round(_mean(deltas), 3)
    out["median_improvement"] = round(statistics.median(deltas), 3)
    if len(deltas) > 1:
        se = statistics.stdev(deltas) / math.sqrt(len(deltas))
        out["se"] = round(se, 3)
        out["t"] = round(out["mean_improvement"] / se, 2) if se else None
    out["challenger_closer"] = sum(1 for d in deltas if d > 0)
    out["champion_closer"] = sum(1 for d in deltas if d < 0)
    out["tied"] = sum(1 for d in deltas if d == 0)
    return out


def all_scoreboards(conn, *, model_version, strategy_version, sport="cfb"):
    """Every scoreboard, each labelled, none summed."""
    import horizons
    return {
        "forecast_quality": forecast_quality(
            conn, model_version=model_version, horizon=horizons.OFFICIAL_HORIZON,
            sport=sport),
        "signal_performance": signal_performance(
            conn, strategy_version=strategy_version, sport=sport),
        "closing_diagnostic": closing_diagnostic(
            conn, strategy_version=strategy_version, sport=sport),
        "user_bets": {"note": "actual wagers are not modelled here and are never "
                              "mixed with the strategy's record"},
    }
