"""
grading.py — the one place a result is decided.

Every W, L, P and unit of profit in this project comes from these functions. They
are pure: no database, no clock, no config. That is deliberate, because grading is
the part of the system nobody eyeballs. A spread graded from the wrong side does
not crash — the record simply fills up with plausible percentages that describe a
different set of wagers than the ones that were made.

THE TWO RULES THIS MODULE EXISTS TO ENFORCE
-------------------------------------------

1. THE SIDE THAT WAS TAKEN IS THE SIDE THAT IS GRADED.

   The previous grader derived the side at grading time:

       edge = model_margin - closing_margin
       result = W if (edge > 0) == (actual - closing > 0) else L

   `ats_pick` — the side actually published — was never read. So when the market
   moved across the model's number the graded side FLIPPED. A model that said
   home by 7, took HOME at a line of 6, and watched the line move to 8 was graded
   as though it had taken AWAY. That is not a strict-enough version of the
   original question; it is a different question about a bet nobody placed.

   Every function here takes `side` explicitly and never infers one.

2. THE LOCKED LINE AND THE CLOSING LINE ARE DIFFERENT QUESTIONS.

   "Did the wager we published win at the number we locked?" and "was that side
   also right about the final market?" have different answers, and the old grader
   answered only the second while the site reported it as the first. Take a side
   at +2.5 that closes +3.5 and loses by 3: the wager won and the closing-line
   version lost. Both facts are worth keeping. Neither may stand in for the other.

   These functions do not know which line they are given. The caller grades twice
   — once at the locked line, once at the close — and stores both.

HOUSE CONVENTION
----------------
A home margin is always from the home team's perspective, in both lines and
results:

    +7  home team favoured by 7 / home team won by 7
    -3  away team favoured by 3 / away team won by 3

This is the convention `db.py` normalizes at ingest and every existing reader
assumes. Nothing here renegotiates it.
"""

# Results. Kept as module constants so a caller cannot invent a fourth one.
WIN = "W"
LOSS = "L"
PUSH = "P"

OVER = "OVER"
UNDER = "UNDER"


def grade_spread_pick(*, side, home_team, away_team, home_margin_line,
                      actual_home_margin):
    """
    Did `side` cover `home_margin_line`? -> 'W' | 'L' | 'P' | None

    `side` is the TEAM NAME that was backed, never a margin and never a
    direction inferred from one. None is returned when the question cannot be
    asked at all — no line, no result, or no recorded side — because an
    ungradeable pick is not a loss.

    Raises ValueError when `side` names a team that is not in this game. That is
    an upstream bug (an alias drift, a mis-joined row), and returning 'L' for it
    would book a defeat for a wager that never existed.
    """
    if side is None or home_margin_line is None or actual_home_margin is None:
        return None
    if side not in (home_team, away_team):
        raise ValueError(
            "side %r is neither the home team (%r) nor the away team (%r)"
            % (side, home_team, away_team))
    if actual_home_margin == home_margin_line:
        return PUSH
    home_covered = actual_home_margin > home_margin_line
    if side == home_team:
        return WIN if home_covered else LOSS
    return LOSS if home_covered else WIN


def grade_total_pick(*, side, total_line, actual_total):
    """
    Did OVER or UNDER win against `total_line`? -> 'W' | 'L' | 'P' | None

    As with the spread, `side` is the direction that was actually published. The
    old grader recomputed it from `model_total - closing_total`, with the same
    flip-on-movement problem.
    """
    if side is None or total_line is None or actual_total is None:
        return None
    side = str(side).upper()
    if side not in (OVER, UNDER):
        raise ValueError("total side %r is neither OVER nor UNDER" % (side,))
    if actual_total == total_line:
        return PUSH
    went_over = actual_total > total_line
    if side == OVER:
        return WIN if went_over else LOSS
    return LOSS if went_over else WIN


def grade_moneyline_pick(*, team, home_team, away_team, actual_home_margin):
    """
    Did `team` win the game outright? -> 'W' | 'L' | 'P' | None

    No odds needed to decide W/L/P. Odds are needed for what it PAID, which is
    `american_profit_units` and a separate question.
    """
    if team is None or actual_home_margin is None:
        return None
    if team not in (home_team, away_team):
        raise ValueError(
            "team %r is neither the home team (%r) nor the away team (%r)"
            % (team, home_team, away_team))
    if actual_home_margin == 0:
        return PUSH
    home_won = actual_home_margin > 0
    if team == home_team:
        return WIN if home_won else LOSS
    return LOSS if home_won else WIN


def american_profit_units(result, odds):
    """
    Profit on ONE unit risked, at American `odds`. -> float | None

    None means the return is unknown, and it is returned whenever the price was
    not recorded. That is the entire point of this function existing separately
    from the W/L above.

    A -110 default is the most attractive wrong answer in this project: it turns
    every winning pick into +0.909 units and produces a published ROI for wagers
    whose actual price nobody ever wrote down. The CFBD feed supplies moneylines
    but not spread or total juice, so most historical spread prices here are
    genuinely unknown and must stay that way.

    Where a synthetic -110 analysis is genuinely wanted, the CALLER passes -110
    deliberately and the resulting metric carries a name that says so
    (`synthetic_roi_assuming_minus110`). It is never supplied here as a default.
    """
    if result == PUSH:
        return 0.0
    if result == LOSS:
        return -1.0
    if result != WIN:
        return None                    # ungraded, or a result we do not know
    if odds is None:
        return None
    try:
        odds = int(odds)
    except (TypeError, ValueError):
        return None
    if odds == 0:                      # not a price; almost certainly a null in disguise
        return None
    return (odds / 100.0) if odds > 0 else (100.0 / abs(odds))


def line_clv(*, side, locked, closing):
    """
    Points of closing-line value on a spread. -> float | None

    Positive means the number taken was better than the number it closed at.
    `side` is 'home' or 'away' (or the team name, resolved by the caller).

        home: closing - locked      taking +3 that closes +4 is +1
        away: locked - closing      taking -3 that closes -2 is +1

    This is POINTS ONLY. It is not interchangeable with price movement: half a
    point across a key number is worth more than half a point at 16, and juice
    can move while the number does not. `price_clv_probability` is the separate
    measure, and the two are never summed.
    """
    if locked is None or closing is None or side is None:
        return None
    s = str(side).lower()
    if s not in ("home", "away"):
        raise ValueError("spread CLV side %r is neither 'home' nor 'away'" % (side,))
    return (closing - locked) if s == "home" else (locked - closing)


def total_clv(*, side, locked, closing):
    """
    Points of closing-line value on a total. -> float | None

    An OVER is cheaper the LOWER the number taken, so it gains when the total
    closes HIGHER than the number locked. An UNDER is the mirror: it gains when
    the total closes LOWER.

        OVER:   closing - locked
        UNDER:  locked - closing
    """
    if locked is None or closing is None or side is None:
        return None
    s = str(side).upper()
    if s not in (OVER, UNDER):
        raise ValueError("total CLV side %r is neither OVER nor UNDER" % (side,))
    return (closing - locked) if s == OVER else (locked - closing)


def implied_probability(odds):
    """American odds -> implied probability including the vig. None if unknown."""
    if odds is None:
        return None
    try:
        odds = int(odds)
    except (TypeError, ValueError):
        return None
    if odds == 0:
        return None
    return (100.0 / (odds + 100.0)) if odds > 0 else (abs(odds) / (abs(odds) + 100.0))


def devig_pair(home_odds, away_odds):
    """
    Two-way market -> (home_fair, away_fair), vig removed proportionally.

    Returns (None, None) unless BOTH sides are known: a one-sided quote cannot be
    de-vigged, and halving a single implied probability is a guess wearing the
    clothes of a calculation.
    """
    ph, pa = implied_probability(home_odds), implied_probability(away_odds)
    if ph is None or pa is None:
        return None, None
    total = ph + pa
    if total <= 0:
        return None, None
    return ph / total, pa / total


def price_clv_probability(*, locked_odds, closing_odds):
    """
    Change in implied probability paid for, at comparable lines. -> float | None

    Positive means the price taken was better. Only meaningful when both prices
    refer to the SAME line; the caller is responsible for that, because this
    module cannot see whether the number moved underneath the price.
    """
    p_locked = implied_probability(locked_odds)
    p_close = implied_probability(closing_odds)
    if p_locked is None or p_close is None:
        return None
    return p_close - p_locked
