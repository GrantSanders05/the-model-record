"""
staking.py — how much to bet, and how much evidence you need before betting at all.

Edge decides WHETHER to bet. Kelly decides HOW MUCH. Most bankrolls die from
over-betting a real edge rather than from having no edge at all, so this module
exists to make the sizing question answerable instead of intuitive.

It also answers the question that matters most right now: how many graded bets
does it take to tell a 54% model from a 52.38% break-even one? The answer is
uncomfortably large, and knowing it is what stops a good month being mistaken
for an edge.
"""

import math

BREAK_EVEN = 0.5238095238095238
DEC_ODDS = 1.909090909090909      # -110 in decimal


def kelly_fraction(p_win, dec_odds=DEC_ODDS):
    """
    Optimal fraction of bankroll for a bet with win probability p_win.

        f* = (b*p - q) / b       where b = dec_odds - 1

    Returns 0 when there is no edge. Full Kelly is the growth-maximizing stake
    and is far too aggressive in practice, because it assumes p_win is known
    exactly -- see `recommended_stake`.
    """
    b = dec_odds - 1.0
    q = 1.0 - p_win
    f = (b * p_win - q) / b
    return max(0.0, f)


def recommended_stake(p_win, bankroll, fraction=0.25, cap=0.02, dec_odds=DEC_ODDS):
    """
    Fractional Kelly with a hard cap.

    Quarter Kelly is the common professional setting. It gives up roughly 25% of
    theoretical growth in exchange for a very large reduction in drawdown, and
    -- crucially -- it stays solvent when your estimate of p_win is wrong, which
    it always is. The cap is a second belt: never risk more than `cap` of the
    bankroll on one game regardless of what the math says.
    """
    f = kelly_fraction(p_win, dec_odds) * fraction
    return round(bankroll * min(f, cap), 2)


def games_needed(true_rate, null_rate=BREAK_EVEN, power=0.80, alpha=0.05):
    """
    Bets required to show `true_rate` beats `null_rate` at the given power.

    Standard one-sided two-proportion sample size. This is the number that
    governs whether a betting record means anything, and it is the reason a
    single season is never enough: a genuinely excellent 55% model still needs
    roughly a thousand graded bets before a 95% interval clears break-even.
    """
    if true_rate <= null_rate:
        return None
    z_a = 1.6448536269514722          # one-sided 95%
    z_b = {0.80: 0.8416, 0.90: 1.2816, 0.95: 1.6449}.get(power, 0.8416)
    p0, p1 = null_rate, true_rate
    num = (z_a * math.sqrt(p0 * (1 - p0)) + z_b * math.sqrt(p1 * (1 - p1))) ** 2
    return math.ceil(num / ((p1 - p0) ** 2))


def risk_of_ruin(p_win, fraction, n_bets=1000, ruin_level=0.5, trials=4000, seed=3):
    """Monte Carlo probability of the bankroll falling below `ruin_level`."""
    import random
    rng = random.Random(seed)
    b = DEC_ODDS - 1.0
    ruined = 0
    for _ in range(trials):
        bank = 1.0
        for _ in range(n_bets):
            stake = bank * fraction
            bank += stake * b if rng.random() < p_win else -stake
            if bank <= ruin_level:
                ruined += 1
                break
    return 100.0 * ruined / trials


def report(observed_rate, n_observed, bankroll=1000.0):
    L = []
    L.append("Observed: %.2f%% on %d graded bets" % (observed_rate, n_observed))
    L.append("Break-even at -110: %.2f%%\n" % (100 * BREAK_EVEN))

    L.append("How many bets to PROVE a given true rate beats break-even (80% power):")
    for r in (0.53, 0.54, 0.55, 0.56, 0.58, 0.60):
        n = games_needed(r)
        L.append("   true %.0f%%  ->  %s bets" % (100 * r, "{:,}".format(n) if n else "never"))

    L.append("\nStake sizing at various win probabilities (bankroll $%.0f, quarter Kelly, 2%% cap):"
             % bankroll)
    for p in (0.5238, 0.53, 0.55, 0.57, 0.60):
        f = kelly_fraction(p)
        L.append("   p=%.0f%%  full Kelly %5.2f%%  ->  quarter-Kelly stake $%.2f"
                 % (100 * p, 100 * f, recommended_stake(p, bankroll)))

    # The realistic failure mode is not "no edge, no bet" -- it is betting the
    # stake your BELIEVED edge justifies while the true rate is lower. Sizing is
    # therefore held fixed at what a believed 54% implies, and only the truth varies.
    believed = kelly_fraction(0.54)
    L.append("\nYou SIZE as if you have 54%. Risk of losing half the bankroll over 1,000 bets\n"
             "if the truth is actually:")
    for p, lbl in ((0.54, "54% - the edge was real"),
                   (0.5238, "52.4% - exactly break-even"),
                   (0.50, "50% - the edge was noise")):
        L.append("   %-30s quarter-Kelly (%.1f%%/bet): %5.1f%%   full Kelly (%.1f%%/bet): %5.1f%%"
                 % (lbl, 100 * believed * 0.25,
                    risk_of_ruin(p, believed * 0.25),
                    100 * believed, risk_of_ruin(p, believed)))
    return "\n".join(L)


if __name__ == "__main__":
    print(report(54.78, 502))
