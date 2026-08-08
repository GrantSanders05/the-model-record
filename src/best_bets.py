"""
best_bets.py — rank today's games by actual expected value, not by gut.

Two markets, two different calculations:

SPREAD VALUE is just the disagreement with the closing number. It is the
weaker signal of the two and it comes with a warning attached: backtesting
showed the results-only model performed WORSE as its disagreement grew, which
is the signature of miscalibration rather than insight. The grade model does
better on large edges, but on 115 games — nowhere near enough to trust.

MONEYLINE VALUE is the more defensible one, because it can be computed as a
genuine expected value rather than a vibe:

  1. Convert the model's predicted margin into a win probability.
  2. Strip the vig out of the two-way moneyline to get the market's FAIR
     probability. Skipping this step is the single most common error in
     amateur EV work -- raw implied probabilities sum to ~105%, so almost
     everything looks like value until the juice is removed.
  3. EV = p_model x payout - (1 - p_model).

Stake sizing is quarter Kelly with a hard cap, from `staking.py`.

Nothing here is advice, and the module says so on the output: until the model's
record clears break-even with its whole confidence interval, every one of these
is a hypothesis being tracked, not a bet that is known to be good.
"""

import argparse
import json
import os

import backtest
import db
import predict
import staking

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def implied_prob(american):
    if american is None:
        return None
    a = float(american)
    return (-a) / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def devig(p_home, p_away):
    """
    Remove the bookmaker's margin, proportionally.

    Raw implied probabilities sum to more than 1 -- that excess IS the vig.
    Comparing a model against un-devigged probabilities makes roughly every
    game look like a bet.
    """
    if p_home is None or p_away is None:
        return p_home, p_away
    tot = p_home + p_away
    if tot <= 0:
        return p_home, p_away
    return p_home / tot, p_away / tot


def payout(american):
    """Profit per 1 unit staked."""
    if american is None:
        return None
    a = float(american)
    return (100.0 / -a) if a < 0 else (a / 100.0)


def dispersion_check(rows):
    """
    Is the model's spread of opinion even comparable to the market's?

    A rating set that has been regressed toward the mean -- placeholder grades
    carried over from last season, say -- produces margins that barely vary.
    Every underdog then looks live, and the EV column fills with enormous
    numbers that are artefacts of under-dispersion rather than value.

    Comparing the standard deviation of model margins against the market's
    catches that in one number, before it becomes a week of bad picks.
    """
    pairs = [(r["model_margin"], r["market_margin"]) for r in rows
             if r.get("market_margin") is not None]
    if len(pairs) < 20:
        return None
    def sd(v):
        m = sum(v) / len(v)
        return (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5
    sd_model, sd_mkt = sd([a for a, _ in pairs]), sd([b for _, b in pairs])
    if sd_mkt == 0:
        return None
    ratio = sd_model / sd_mkt
    return {"sd_model": sd_model, "sd_market": sd_mkt, "ratio": ratio,
            "ok": 0.6 <= ratio <= 1.6, "n": len(pairs)}


def rank(conn, sport, config, season=None, week=None, bankroll=1000.0):
    picks = predict.generate(conn, sport, config, week=week, season=season)
    games = {g["game_id"]: g for g in backtest.load_games(conn, sport)}
    out = []

    for p in picks:
        g = games.get(p["game_id"], {})
        home_ml, away_ml = g.get("home_ml"), g.get("away_ml")
        wp_home = predict.margin_to_win_prob(p["model_margin"], sport)

        rec = {
            "game_id": p["game_id"], "week": p["week"], "kickoff": p["kickoff"],
            "away": p["away"], "home": p["home"],
            "model_margin": p["model_margin"], "market_margin": p["market_margin"],
            "spread_edge": p["edge"], "ats_pick": p["ats_pick"],
            "model_total": p["model_total"], "market_total": p["market_total"],
            "ou_pick": p["ou_pick"],
            "model_win_prob_home": round(100 * wp_home, 1),
            "ml_pick": None, "ml_ev": None, "ml_fair_prob": None,
            "ml_odds": None, "stake": None,
        }

        ph, pa = devig(implied_prob(home_ml), implied_prob(away_ml))
        if ph is not None:
            # Evaluate BOTH sides and keep the better one; a model can be on the
            # favourite and still find the value on the dog.
            cands = []
            for side, p_model, p_mkt, odds in (
                    (p["home"], wp_home, ph, home_ml),
                    (p["away"], 1 - wp_home, pa, away_ml)):
                b = payout(odds)
                if b is None:
                    continue
                ev = p_model * b - (1 - p_model)
                cands.append((ev, side, p_model, p_mkt, odds))
            if cands:
                ev, side, p_model, p_mkt, odds = max(cands)
                rec.update({
                    "ml_pick": side, "ml_ev": round(100 * ev, 2),
                    "ml_fair_prob": round(100 * p_mkt, 1),
                    "model_prob": round(100 * p_model, 1),
                    "ml_odds": odds,
                    "stake": staking.recommended_stake(p_model, bankroll) if ev > 0 else 0.0,
                })
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--config", default="config/cfb_grades.json")
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--by", default="ml", choices=["ml", "spread"])
    ap.add_argument("--out", help="write full ranking to JSON")
    args = ap.parse_args()

    path = args.config if os.path.isabs(args.config) else os.path.join(ROOT, args.config)
    config = json.load(open(path)) if os.path.exists(path) else {}
    conn = db.connect()
    rows = rank(conn, args.sport, config, args.season, args.week, args.bankroll)
    if not rows:
        print("No upcoming games. Off-season, or the schedule isn't fetched yet.")
        return

    disp = dispersion_check(rows)
    if disp and not disp["ok"]:
        print("=" * 78)
        print("  WARNING — THESE PICKS ARE NOT USABLE YET")
        print("  Model margins vary %.1f pts; the market's vary %.1f (ratio %.2f)."
              % (disp["sd_model"], disp["sd_market"], disp["ratio"]))
        if disp["ratio"] < 0.6:
            print("  The ratings are UNDER-DISPERSED — the model can barely tell teams")
            print("  apart, so every underdog looks like value and the EV column is")
            print("  measuring that flaw, not an edge. This is what placeholder grades")
            print("  look like. Grade film, then re-run.")
        else:
            print("  The ratings are OVER-DISPERSED — margins are far more extreme than")
            print("  the market's. Check for a mis-entered grade or a scale problem.")
        print("=" * 78 + "\n")

    if args.by == "ml":
        rows.sort(key=lambda r: -(r["ml_ev"] if r["ml_ev"] is not None else -999))
        print("TOP MONEYLINE VALUE  (EV per 1u staked, vig removed)\n")
        print("%-4s %-22s %-22s %8s %8s %8s %8s %7s" % (
            "wk", "away", "home", "pick", "odds", "modelP", "fairP", "EV%"))
        print("-" * 96)
        for r in rows[:args.top]:
            if r["ml_ev"] is None:
                continue
            print("%-4s %-22s %-22s %8s %8s %7.1f%% %7.1f%% %+6.2f" % (
                r["week"], r["away"][:22], r["home"][:22],
                (r["ml_pick"] or "")[:8], r["ml_odds"],
                r.get("model_prob", 0), r["ml_fair_prob"], r["ml_ev"]))
    else:
        rows.sort(key=lambda r: -(abs(r["spread_edge"]) if r["spread_edge"] else 0))
        print("TOP SPREAD DISAGREEMENT\n")
        print("%-4s %-22s %-22s %8s %8s %7s  %s" % (
            "wk", "away", "home", "model", "market", "edge", "pick"))
        print("-" * 90)
        for r in rows[:args.top]:
            if r["spread_edge"] is None:
                continue
            print("%-4s %-22s %-22s %+8.1f %+8.1f %+7.1f  %s" % (
                r["week"], r["away"][:22], r["home"][:22],
                r["model_margin"], r["market_margin"], r["spread_edge"],
                r["ats_pick"] or "-"))

    print("\n  %d games ranked. Stakes are quarter-Kelly on a $%.0f bankroll, 2%% cap."
          % (len(rows), args.bankroll))
    print("  These are hypotheses being TRACKED, not bets known to be good: the")
    print("  model's edge has not yet cleared break-even with its full interval.")

    if args.out:
        o = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(os.path.dirname(o), exist_ok=True)
        json.dump(rows, open(o, "w"), indent=2)
        print("  full ranking -> %s" % o)


if __name__ == "__main__":
    main()
