"""
tracking.py — what the record actually says, cut every way that could change the answer.

The headline number is one line: 55% ATS. That line is also where almost every
betting model dies, because it hides the two questions that matter. Is it real, or
is it 40 games of variance? And is it real EVERYWHERE, or is one segment carrying
a set of segments that are break-even?

So nothing here reports a percentage on its own. Every rate carries:

  n              how many decisions it is built on
  a 95% interval Wilson, which stays honest at small n where the normal
                 approximation says a 3-1 start is a 75% model
  clears         whether the WHOLE interval sits above break-even

`clears` is the only column that answers "do I have an edge". A 58% rate on 24
bets does not clear. A 54% rate on 900 does. The point-estimate ordering of those
two is backwards from their evidential ordering, which is exactly why the point
estimate alone is a trap.

CLOSING LINE VALUE is computed alongside, and it is the one number that means
something before the record does. A win rate needs ~1,000 bets to separate 54%
from break-even; CLV separates a real edge from noise in dozens, because it does
not depend on whether the ball bounced your way. If the model consistently bets
numbers better than the closing line it is finding something. If it does not, a
winning record is luck that will end.
"""

import metrics

BREAK_EVEN = metrics.BREAK_EVEN
WIN_UNITS = metrics.WIN_UNITS


# ── one scored set of decisions ────────────────────────────────────────────────

def rate(w, l, p=0, win_units=WIN_UNITS, break_even=BREAK_EVEN):
    """
    A record, with the interval that says whether to believe it.

    Pushes are excluded from the rate and reported separately: a push is not a
    half-win, it is a bet that did not happen, and folding it in drags every rate
    toward 50% by an amount that depends on how many pushes you had.
    """
    n = w + l
    out = {"w": w, "l": l, "push": p, "n": n, "decided": n,
           "pct": None, "ci95": None, "clears": None, "units": None, "roi": None}
    if not n:
        return out
    lo, hi = metrics.wilson_ci(w, n)
    units = w * win_units - l
    out.update({
        "pct": round(100.0 * w / n, 2),
        "ci95": [round(100 * lo, 2), round(100 * hi, 2)],
        "clears": bool(lo > break_even),
        "units": round(units, 2),
        "roi": round(100.0 * units / n, 2),
    })
    return out


def _tally(rows, key, win_units=WIN_UNITS):
    w = sum(1 for r in rows if r[key] == "W")
    l = sum(1 for r in rows if r[key] == "L")
    p = sum(1 for r in rows if r[key] == "P")
    return rate(w, l, p, win_units=win_units)


def american_units(odds):
    """Profit on a 1-unit win at American odds. -110 -> 0.909, +150 -> 1.5."""
    if odds is None:
        return None
    a = float(odds)
    return (100.0 / -a) if a < 0 else (a / 100.0)


def moneyline_rate(rows):
    """
    Straight-up picks, scored two ways, because one of them is a vanity metric.

    HIT RATE is how often the pick won. It looks impressive and means very little:
    always taking the favourite hits about 72% in college football and loses money.

    UNITS is the same picks priced at the odds actually locked. That is the number
    that can be negative while the hit rate is 70%, and it is the honest one.
    """
    w = sum(1 for r in rows if r["ml_result"] == "W")
    l = sum(1 for r in rows if r["ml_result"] == "L")
    p = sum(1 for r in rows if r["ml_result"] == "P")
    out = rate(w, l, p, win_units=1.0, break_even=0.5)
    priced = [r for r in rows if r["ml_result"] in ("W", "L")
              and r["ml_odds_at_pick"] is not None]
    if priced:
        units = sum(american_units(r["ml_odds_at_pick"]) if r["ml_result"] == "W" else -1.0
                    for r in priced)
        out["units"] = round(units, 2)
        out["roi"] = round(100.0 * units / len(priced), 2)
        out["priced_n"] = len(priced)
    else:
        # Without prices this is a hit rate and nothing more. Say so rather than
        # printing a units figure derived from an assumed -110 that nobody offered.
        out["units"] = None
        out["roi"] = None
        out["priced_n"] = 0
    return out


# ── closing line value ─────────────────────────────────────────────────────────

def clv_of(row):
    """
    Points of closing line value on the spread pick, from the pick's own side.

    Positive means the number moved toward us after we bet it -- we took +3 and it
    closed +4, or laid -7 and it closed -6.5. This is computable the moment a game
    kicks off and does not care who won, which is what makes it the early signal.
    """
    at, close = row["market_margin_at_pick"], row["closing_margin"]
    if at is None or close is None or not row["ats_pick"]:
        return None
    # Both stored home-perspective, so derive each side explicitly rather than by
    # intuition. Backing the HOME team means laying `at` points and the market
    # ending on `close`; laying fewer than the market settled on is the win, so
    # CLV = close - at (laid 3, closed 10 -> +7). Backing the AWAY team means
    # receiving `at` and the market ending on `close`; receiving more is the win,
    # so CLV = at - close (took +3, closed +10 -> -7, a worse number).
    #
    # These two were swapped, and it was invisible: CLV kept reporting a clean
    # positive edge, which is exactly the shape of the good news nobody questions.
    if row["ats_pick"] == row["home_team"]:
        return round(close - at, 2)
    if row["ats_pick"] == row["away_team"]:
        return round(at - close, 2)
    return None


def clv_summary(rows):
    vals = [v for v in (clv_of(r) for r in rows) if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "beat_pct": None, "positive": 0, "negative": 0}
    pos = sum(1 for v in vals if v > 0)
    neg = sum(1 for v in vals if v < 0)
    lo, hi = metrics.wilson_ci(pos, pos + neg) if (pos + neg) else (0.0, 0.0)
    return {
        "n": len(vals),
        "mean": round(sum(vals) / len(vals), 3),
        "positive": pos, "negative": neg, "flat": len(vals) - pos - neg,
        "beat_pct": round(100.0 * pos / (pos + neg), 2) if (pos + neg) else None,
        "beat_ci95": [round(100 * lo, 2), round(100 * hi, 2)] if (pos + neg) else None,
    }


# ── calibration ────────────────────────────────────────────────────────────────

def calibration(rows):
    """
    Regress actual margin on predicted margin. Slope 1.0 is perfect calibration.

    A slope well under 1 means the model's big numbers are too big -- it is
    directionally informed and numerically overconfident, which is a scaling
    problem and not an ignorance problem. That distinction is the difference
    between "grade more film" and "multiply by 0.62", and only this tells them
    apart.
    """
    pairs = [(r["model_margin"], r["actual_margin"]) for r in rows
             if r["model_margin"] is not None and r["actual_margin"] is not None]
    if len(pairs) < 10:
        return None
    slope, intercept, r = metrics.linreg([a for a, _ in pairs], [b for _, b in pairs])
    if slope is None:
        return None
    err = [a - b for a, b in pairs]
    return {
        "n": len(pairs),
        "slope": round(slope, 3),
        "intercept": round(intercept, 2),
        "r": round(r, 3),
        "rmse": round((sum(e * e for e in err) / len(err)) ** 0.5, 2),
        "bias": round(sum(err) / len(err), 3),
    }


# ── the cuts ───────────────────────────────────────────────────────────────────

def _edge_bucket(r):
    e = r["model_margin"] - r["closing_margin"] if (
        r["model_margin"] is not None and r["closing_margin"] is not None) else None
    if e is None:
        return None
    e = abs(e)
    for lo, hi, label in ((0, 1, "under 1"), (1, 3, "1 – 3"), (3, 6, "3 – 6"),
                          (6, 10, "6 – 10"), (10, 1e9, "10+")):
        if lo <= e < hi:
            return label
    return None


def _side_bucket(r):
    if r["closing_margin"] is None or not r["ats_pick"]:
        return None
    home_fav = r["closing_margin"] > 0
    picked_home = r["ats_pick"] == r["home_team"]
    return "favourite" if (home_fav == picked_home) else "underdog"


def _venue_bucket(r):
    if not r["ats_pick"]:
        return None
    return "home team" if r["ats_pick"] == r["home_team"] else "away team"


def _by(rows, fn, order=None):
    groups = {}
    for r in rows:
        k = fn(r)
        if k is None:
            continue
        groups.setdefault(k, []).append(r)
    keys = order or sorted(groups)
    out = []
    for k in keys:
        if k not in groups:
            continue
        g = groups[k]
        out.append({"key": str(k), "ats": _tally(g, "ats_result"),
                    "ou": _tally(g, "ou_result"), "ml": moneyline_rate(g),
                    "clv": clv_summary(g)})
    return out


def weekly(rows, week_labels=None):
    """Per week, in order. The cut Grant asked for first and the one he will read."""
    labels = week_labels or {}
    out = _by(rows, lambda r: labels.get(r["game_id"], r["week"]),
              order=sorted({labels.get(r["game_id"], r["week"]) for r in rows
                            if labels.get(r["game_id"], r["week"]) is not None}))
    # Running totals, so a week can be read against the season to date rather than
    # in isolation -- a 3-6 week means something different in a 55% season.
    w = l = 0
    for row in out:
        w += row["ats"]["w"]
        l += row["ats"]["l"]
        row["cumulative"] = rate(w, l)
    return out


def summary(conn, sport, season=None, week_labels=None):
    """Everything, from the graded ledger only. Ungraded picks are not results."""
    q = ("SELECT * FROM picks_log WHERE sport=? AND graded_at IS NOT NULL")
    args = [sport]
    if season:
        q += " AND season=?"
        args.append(season)
    rows = [dict(r) for r in conn.execute(q + " ORDER BY kickoff, game_id", args)]

    # Locked but not yet playable, reported separately so an empty record reads as
    # "nothing has finished" rather than as "the model has no results".
    pend = conn.execute(
        "SELECT COUNT(*) c FROM picks_log WHERE sport=? AND graded_at IS NULL",
        (sport,)).fetchone()["c"]

    if not rows:
        return {"graded": 0, "pending": pend, "empty": True}

    for r in rows:
        r["clv"] = clv_of(r)

    return {
        "empty": False,
        "graded": len(rows),
        "pending": pend,
        "first_kickoff": rows[0]["kickoff"],
        "last_kickoff": rows[-1]["kickoff"],
        "overall": {"ats": _tally(rows, "ats_result"),
                    "ou": _tally(rows, "ou_result"),
                    "ml": moneyline_rate(rows),
                    "clv": clv_summary(rows)},
        "weekly": weekly(rows, week_labels),
        "by_edge": _by(rows, _edge_bucket,
                       order=["under 1", "1 – 3", "3 – 6", "6 – 10", "10+"]),
        "by_side": _by(rows, _side_bucket, order=["favourite", "underdog"]),
        "by_venue": _by(rows, _venue_bucket, order=["home team", "away team"]),
        "by_config": _by(rows, lambda r: r["config_label"]),
        "calibration": calibration(rows),
        "break_even": round(100 * BREAK_EVEN, 2),
        # Every graded pick, so the app can show the working rather than only the
        # totals. A record nobody can audit is a claim.
        "rows": [{
            "game_id": r["game_id"], "week": (week_labels or {}).get(r["game_id"], r["week"]),
            "kickoff": r["kickoff"], "away": r["away_team"], "home": r["home_team"],
            "model_margin": r["model_margin"], "line_at_pick": r["market_margin_at_pick"],
            "closing": r["closing_margin"], "actual": r["actual_margin"],
            "ats_pick": r["ats_pick"], "ats": r["ats_result"],
            "ou_pick": r["ou_pick"], "ou": r["ou_result"],
            "ml_pick": r["ml_pick"], "ml": r["ml_result"], "ml_odds": r["ml_odds_at_pick"],
            "clv": r["clv"], "config": r["config_label"],
        } for r in rows],
    }
