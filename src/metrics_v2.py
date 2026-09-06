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


def signal_performance(conn, *, strategy_version, sport="cfb"):
    """
    B. What the strategy actually did, at the numbers it locked.

    ROI is over signals whose price is KNOWN. Where no price was recorded there
    is no return to state, and `roi` is None rather than a figure computed from
    an assumed -110.
    """
    import signals as sig
    rec = sig.official_record(conn, strategy_version=strategy_version, sport=sport)
    n = (rec.get("locked_w") or 0) + (rec.get("locked_l") or 0)
    rec["locked_ci95"] = _wilson(rec.get("locked_w") or 0, n)
    rec["break_even"] = 52.38
    rec["roi_basis"] = ("exact recorded prices" if rec.get("priced_n")
                        else "no prices were recorded, so there is no ROI")
    return rec


def closing_diagnostic(conn, *, strategy_version, sport="cfb"):
    """
    C. The same side, at the close. A DIAGNOSTIC and not a wager record.

    Beating the close is the leading indicator most worth watching, and it is not
    money. Labelled here so it cannot be quoted as the strategy's return.
    """
    import signals as sig
    rec = sig.official_record(conn, strategy_version=strategy_version, sport=sport)
    n = (rec.get("close_w") or 0) + (rec.get("close_l") or 0)
    return {
        "strategy_version": strategy_version,
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
         "       g.home_score, g.away_score"
         "  FROM forecast_log f JOIN games g ON g.game_id=f.game_id"
         " WHERE g.sport=? AND f.model_version IN (?,?)"
         "   AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL")
    args = [sport, champion_version, challenger_version]
    if horizon:
        q += " AND f.horizon=?"
        args.append(horizon)
    by_game = {}
    for r in conn.execute(q, args):
        actual = r["home_score"] - r["away_score"]
        if r["pred_home_margin"] is None:
            continue
        by_game.setdefault(r["game_id"], {})[r["model_version"]] = abs(
            actual - r["pred_home_margin"])
    both = [(v[champion_version], v[challenger_version]) for v in by_game.values()
            if champion_version in v and challenger_version in v]
    out = {"champion": champion_version, "challenger": challenger_version,
           "paired_n": len(both), "horizon": horizon}
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
