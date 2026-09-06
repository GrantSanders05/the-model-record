"""
form_quality.py — E005. Team form as a continuous thing, not a poll threshold.

THE CHAMPION'S RULE STAYS THE CHAMPION'S RULE. This is a challenger, registered
beside it, and the existing quality points are not touched. The current rule was
fitted, tested and beat both a 3,456-point grid search and a regression on hand
numbers; it is not being replaced on a hunch.

WHAT IT IS CHALLENGING. The quality term awards +5 for beating a top-5 side and
+3 for a top-25 side, so beating #25 and beating #26 differ by three points and
nothing in between exists. An ordinal poll rank is not a continuous measure of
strength, and a poll already contains public expectation and results that are
priced elsewhere in the model.

    performance_residual = actual_margin - pregame_expected_margin
    form_t = decay * form_(t-1) + rate * winsorize(performance_residual)

THE INFORMATION SET IS A CHOICE AND IT IS DECLARED. `expected_from` says whether
the expectation is the MARKET's number before the game or the MODEL's own. They
answer different questions:

    market   "how did this team do against what everyone thought" — team form
             that is explicitly market-informed, which is fine and must be
             LABELLED, because a model fed market residuals cannot then be said
             to have beaten the market on its own.
    model    "how did this team do against what WE thought" — self-contained,
             and noisier, because it inherits the model's own errors.

GUARDRAILS, all of which exist because the failure mode is a runaway:

  * one game's residual is winsorized, so a 60-point win does not become a
    permanent opinion;
  * early-season form is shrunk hard toward zero, because two games is not a
    form;
  * nothing updates until a game is FINAL;
  * no future ranking, no future result — `observe` is only ever called on games
    already played, in order.
"""

DEFAULT_DECAY = 0.85          # a game's influence halves in about four weeks
DEFAULT_RATE = 0.35
WINSOR = 21.0                 # three touchdowns; beyond it a game says the same thing
SHRINK_GAMES = 4.0            # form is scaled by n / (n + this) early on

MARKET = "market"
MODEL = "model"


class TeamForm:
    """
    Exponentially decayed performance residual per team.

    Walk-forward by construction: `value_for` reads the state accrued from games
    already observed, and `observe` is called after a prediction, never before.
    """

    def __init__(self, *, decay=DEFAULT_DECAY, rate=DEFAULT_RATE,
                 winsor=WINSOR, shrink_games=SHRINK_GAMES,
                 expected_from=MARKET):
        if expected_from not in (MARKET, MODEL):
            raise ValueError("expected_from must be %r or %r" % (MARKET, MODEL))
        self.decay = decay
        self.rate = rate
        self.winsor = winsor
        self.shrink_games = shrink_games
        self.expected_from = expected_from
        self.form = {}
        self.games = {}
        self._season = None

    def new_season(self, season):
        # Form does not carry across a season. Rosters turn over, and last
        # November's form is a statement about players who have left.
        self._season = season
        self.form.clear()
        self.games.clear()

    def value_for(self, team):
        """
        The team's form, shrunk for how little of it there is. -> float

        Two games is not a form. The shrink is n/(n+k), so a team with one game
        carries a fifth of its raw value and a team with twelve carries three
        quarters of it.
        """
        n = self.games.get(team, 0)
        if not n:
            return 0.0
        return self.form.get(team, 0.0) * (n / (n + self.shrink_games))

    def diff(self, home_team, away_team):
        return self.value_for(home_team) - self.value_for(away_team)

    def observe(self, *, home_team, away_team, actual_home_margin,
                expected_home_margin):
        """Update both teams from a FINAL game. Ignores anything incomplete."""
        if actual_home_margin is None or expected_home_margin is None:
            return
        resid = actual_home_margin - expected_home_margin
        resid = max(-self.winsor, min(self.winsor, resid))
        for team, sign in ((home_team, 1.0), (away_team, -1.0)):
            prev = self.form.get(team, 0.0)
            self.form[team] = self.decay * prev + self.rate * sign * resid
            self.games[team] = self.games.get(team, 0) + 1

    def state(self):
        return {"decay": self.decay, "rate": self.rate, "winsor": self.winsor,
                "shrink_games": self.shrink_games,
                "expected_from": self.expected_from,
                "teams": len(self.form)}
