"""
pro_models.py — the approaches serious sports modelers actually use.

Grant asked what the best people in the world do differently. Stripped of
mystique, it is four things, and only one of them is a fancy model:

  1. EFFICIENCY, NOT RESULTS. Points and win/loss are noisy summaries of what
     happened. Per-play efficiency (EPA in the NFL, PPA here) is a far better
     estimate of team strength because it uses ~150 observations per game
     instead of 1. This is what SP+, FEI and FPI are built on.

  2. OPPONENT ADJUSTMENT. Raw efficiency mostly measures schedule. Every serious
     rating solves for team strengths *simultaneously*, so beating a good
     defense counts more than beating a bad one.

  3. ANCHOR TO THE MARKET. The single most important and least glamorous
     technique. The closing line is the best public forecast that exists; the
     professional move is not to out-predict it from scratch but to start FROM
     it and deviate only where you have a specific reason. A model that shrinks
     90% toward the market and 10% toward its own view bets rarely and wins
     more often than one that backs its own number every week.

  4. BET SIZING. Edge decides *whether* to bet; Kelly decides *how much*. Most
     bankrolls die from over-betting a real edge, not from having no edge.

What they are NOT doing is finding a magic formula. There isn't one. The work
is better inputs, honest validation, and disciplined staking.

This module implements 1–3. Kelly sizing lives in `staking.py`.
"""

import math
from collections import defaultdict


class PPARater:
    """
    Opponent-adjusted per-play efficiency — the SP+ / FPI family.

    Maintains, for every team, a rolling estimate of offensive and defensive
    PPA adjusted for the quality of opponent faced. Ratings update only from
    games already played, so it stays walk-forward.

    The adjustment is deliberately simple and iterative rather than a full
    ridge solve: for each completed game, a team's offensive performance is
    credited against the opponent's current defensive rating and vice versa.
    Run over a season this converges to something very close to the matrix
    solution at a fraction of the complexity, and it updates game by game
    instead of needing a refit.
    """

    def __init__(self, cfg, stats_by_game):
        self.cfg = cfg
        self.stats = stats_by_game          # {(game_id, team): row}
        self.off = defaultdict(list)        # team -> [adjusted off ppa]
        self.dfn = defaultdict(list)
        self.prior_off, self.prior_def = {}, {}
        self.league_off = []

    def new_season(self, season):
        c = self.cfg.get("ppa_carryover", 0.35)
        if self.league_off:
            for t, v in self.off.items():
                if v:
                    self.prior_off[t] = sum(v) / len(v)
            for t, v in self.dfn.items():
                if v:
                    self.prior_def[t] = sum(v) / len(v)
        self.off.clear()
        self.dfn.clear()
        self.league_off = []
        self._carry = c

    def _lg(self):
        return sum(self.league_off) / len(self.league_off) if self.league_off else 0.0

    def _rate(self, table, prior, team):
        n_prior = self.cfg.get("ppa_prior_games", 4.0)
        lg = self._lg()
        base = prior.get(team)
        pv = lg if base is None else self._carry * base + (1 - self._carry) * lg
        series = table[team]
        if not series:
            return pv
        return (sum(series) + pv * n_prior) / (len(series) + n_prior)

    def net(self, team):
        return self._rate(self.off, self.prior_off, team) - self._rate(self.dfn, self.prior_def, team)

    def strength(self, game):
        return (self.net(game["home_team"]) - self.net(game["away_team"])) \
            * self.cfg.get("ppa_points_per_ppa", 55.0)

    def observe(self, game):
        if game["home_score"] is None:
            return
        for team, opp in ((game["home_team"], game["away_team"]),
                          (game["away_team"], game["home_team"])):
            row = self.stats.get((game["game_id"], team))
            if not row:
                continue
            o, d = row.get("off_ppa"), row.get("def_ppa")
            if o is None or d is None:
                continue
            # Credit performance against the opponent's current rating: a good
            # offensive day against a strong defense is worth more.
            opp_def = self._rate(self.dfn, self.prior_def, opp)
            opp_off = self._rate(self.off, self.prior_off, opp)
            self.off[team].append(o + (opp_def - self._lg()))
            self.dfn[team].append(d - (opp_off - self._lg()))
            self.league_off.append(o)


def market_anchor(pred_margin, market_margin, weight):
    """
    Shrink a model's number toward the closing line.

    weight = 0.0  -> pure model
    weight = 1.0  -> the market itself (zero disagreement, zero bets)

    This is the technique that most separates professional models from hobby
    ones, and it is almost free to implement. Its effect is to bet rarely and
    only where the model's disagreement is large enough to survive being pulled
    most of the way back to the market's view.
    """
    if market_margin is None or weight <= 0:
        return pred_margin
    return weight * market_margin + (1 - weight) * pred_margin


def load_stats(conn, sport="cfb"):
    out = {}
    for r in conn.execute(
            "SELECT game_id, team, off_ppa, def_ppa, off_pass_ppa, off_rush_ppa, "
            "def_pass_ppa, def_rush_ppa FROM team_game_stats WHERE sport=?", (sport,)):
        out[(r["game_id"], r["team"])] = dict(r)
    return out
