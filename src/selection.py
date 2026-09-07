"""
selection.py — was this a BEST BET, and is that answer written down?

WHY THIS IS A STORED FACT AND NOT A QUERY. `best_bets.qualifies` decides what
the board offers. If the record asked the same question again at render time,
then every future change to `MIN_EDGE` would silently rewrite what the model is
recorded as having bet — a season could be improved by editing a constant. So
the answer is computed once, stamped with the rule version that produced it, and
stored beside the pick. Exactly the reason `ats_result_at_pick` is stored rather
than regraded, and `grading_version` sits next to it.

The backfill below is the one exception, and a narrow one: it fills rows that
predate the column from facts those rows already carry — the model's number, the
line it locked at, the week, and whether the film answered. It derives nothing
new and it never overwrites a value that is already there.
"""

import best_bets
import db

SELECTION_VERSION = best_bets.SELECTION_VERSION

# The market a Best Bet may be taken in. Totals are excluded and it is not a
# close call: replaying 2025, the totals model gets WORSE as it disagrees more --
# 52.2% at any disagreement, 48.8% past four points, 46.9% past five. That is the
# opposite of the spread's shape and the signature of a model with no edge whose
# errors are being mistaken for opinions. Week 1 2026 agreed: 31-39.
BEST_BET_MARKETS = ("spread",)


def classify(*, market, week, model_margin, line, borrowed):
    """
    -> (best_bet: bool, reason: str|None)

    Every argument is a fact recorded at lock time. Nothing is looked up, so the
    same inputs always give the same answer however long afterwards it is asked.
    """
    if market not in BEST_BET_MARKETS:
        return False, "market"
    pick = {"unrated": bool(borrowed), "market_margin": line, "week": week,
            "model_margin": model_margin,
            "edge": None if (model_margin is None or line is None)
                    else model_margin - line}
    reason = best_bets.decline_reason(pick)
    return reason is None, reason


LABEL = dict(best_bets.DECLINE_LABEL,
             market="totals are not offered — the model has no measured edge on them")


def graded_teams(conn, sport="cfb"):
    """{season: {team, ...}} — who the film actually answered for."""
    out = {}
    for r in conn.execute("SELECT DISTINCT season, team FROM grades WHERE sport=?",
                          (sport,)):
        out.setdefault(r["season"], set()).add(r["team"])
    return out


def borrowed_for(have, home, away):
    """
    Did Elo answer this game rather than the film? -> bool

    ONE DEFINITION, used by both the signal path and the ledger path. They had
    two, and the two disagreed on 48 of 99 week-1 picks.

    `forecast_log.borrowed_fallback` looks like the authority and is not, for the
    rows that matter: the V2 migration stamped 0 on all 99 legacy forecasts
    without ever asking the question, so Kansas State-Nicholls and every other
    FCS game came back as fully graded. A column that was defaulted rather than
    measured is not evidence, and `STRATEGY_V0` gates on this exact field.

    Grade coverage is the real condition and it is the one the rater itself
    applies: `GradeRater.strength` returns None -- and Elo takes over -- when
    either side has no grade snapshot. Read from the same table, so the two
    cannot drift again.
    """
    return (home not in have) or (away not in have)


def backfill(conn, *, commit=True):
    """
    Fill the flag on rows recorded before it existed. -> {table: n}

    Idempotent: `WHERE selection_version IS NULL` means a second run touches
    nothing, and a row already classified keeps the version it was classified
    under even after the rule changes.
    """
    filled = {"signal_log": 0, "picks_log": 0, "borrowed_disagreements": 0}
    have_by_season = graded_teams(conn)

    rows = conn.execute(
        "SELECT s.signal_id, s.market, s.line, f.pred_home_margin,"
        "       f.borrowed_fallback, g.week, g.season, g.home_team, g.away_team"
        "  FROM signal_log s"
        "  LEFT JOIN forecast_log f ON f.forecast_id = s.forecast_id"
        "  LEFT JOIN games g        ON g.game_id     = s.game_id"
        " WHERE s.selection_version IS NULL").fetchall()
    for r in rows:
        have = have_by_season.get(r["season"], set())
        borrowed = borrowed_for(have, r["home_team"], r["away_team"])
        if bool(r["borrowed_fallback"]) != borrowed:
            filled["borrowed_disagreements"] += 1
        ok, why = classify(market=r["market"], week=r["week"],
                           model_margin=r["pred_home_margin"], line=r["line"],
                           borrowed=borrowed)
        conn.execute("UPDATE signal_log SET best_bet=?, decline_reason=?,"
                     " selection_version=? WHERE signal_id=?",
                     (1 if ok else 0, why, SELECTION_VERSION, r["signal_id"]))
        filled["signal_log"] += 1

    # The legacy ledger keeps its own copy, because the public page's pending
    # table and the week-by-week breakdown read picks_log directly, and a record
    # filtered in one place and not the other is two records.
    picks = conn.execute(
        "SELECT game_id, season, week, home_team, away_team, model_margin,"
        "       market_margin_at_pick"
        "  FROM picks_log WHERE selection_version IS NULL").fetchall()
    for r in picks:
        have = have_by_season.get(r["season"], set())
        ok, why = classify(market="spread", week=r["week"],
                           model_margin=r["model_margin"],
                           line=r["market_margin_at_pick"],
                           borrowed=borrowed_for(have, r["home_team"], r["away_team"]))
        conn.execute("UPDATE picks_log SET best_bet=?, decline_reason=?,"
                     " selection_version=? WHERE game_id=?",
                     (1 if ok else 0, why, SELECTION_VERSION, r["game_id"]))
        filled["picks_log"] += 1

    if commit:
        conn.commit()
    return filled


def summary(conn, sport="cfb", season=None):
    """
    How many picks each decline reason accounts for. For the page's footnote.

    KEYED ON `best_bet`, NOT ON `decline_reason` BEING NULL. Those are different
    questions and the first version asked the wrong one: a row that has never
    been classified also has a NULL reason, so 934 unclassified fixture picks
    were reported as 934 best bets while `official_record(selection="best")` --
    which requires `best_bet = 1` -- counted none of them. The summary said the
    board was full and the record said it was empty, from one table.

    Unclassified rows get their own key rather than being folded into either
    answer, because "we have not decided" is not "no".
    """
    q = ("SELECT best_bet, decline_reason, COUNT(*) n FROM picks_log"
         " WHERE sport=? AND " + db.NOT_VOIDED)
    args = [sport]
    if season:
        q += " AND season=?"
        args.append(season)
    out = {}
    for r in conn.execute(q + " GROUP BY best_bet, decline_reason", args):
        if r["best_bet"] is None:
            key = "unclassified"
        elif r["best_bet"]:
            key = "best"
        else:
            key = r["decline_reason"] or "declined"
        out[key] = out.get(key, 0) + r["n"]
    return out


if __name__ == "__main__":
    conn = db.connect()
    print("backfilled:", backfill(conn))
    print("by reason:  ", summary(conn))
