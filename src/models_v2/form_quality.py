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


# ── the as-of form state, replayed rather than stored ────────────────────────
#
# A game is only allowed into a team's form once it is genuinely OVER at the
# instant being modelled. Kickoff alone is not enough: a game that started two
# hours before the snapshot is still being played, and its final margin is a
# fact from the future. SETTLE_HOURS is the guard, and it is deliberately longer
# than a football game so a four-overtime Saturday cannot leak backwards.
SETTLE_HOURS = 6

# The expectation for a completed game is that game's stored market number. It is
# a real pregame line for a game that has finished, so it is known at any later
# instant — but it is one preferred provider at an unrecorded time, not an
# exact-horizon snapshot, and the label travels with the feature so the
# limitation is never rediscovered as a surprise.
MARKET_TIMING = "unknown_historical_current"


def settled_before(kickoff, as_of):
    """True when a game with this kickoff had certainly finished by `as_of`."""
    import datetime as _dt
    if not kickoff or not as_of:
        return False
    def _p(s):
        s = str(s).replace("Z", "+00:00")
        try:
            d = _dt.datetime.fromisoformat(s)
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)
    k, a = _p(kickoff), _p(as_of)
    if k is None or a is None:
        return False
    return k + _dt.timedelta(hours=SETTLE_HOURS) <= a


_CACHE = {}


def form_asof(conn, sport, season, as_of, *, expected_from=MARKET, params=None):
    """
    Team form as it stood at `as_of`, replayed from finished games. -> TeamForm

    Replayed rather than stored on purpose. A stored form table is a second copy
    of a derived fact, and the failure it invites is the one this repository has
    hit repeatedly: the writer stops, the reader keeps returning the last value,
    and nothing looks wrong. Replaying ~800 rows costs nothing and cannot go
    stale.
    """
    key = (id(conn), sport, season, as_of, expected_from,
           tuple(sorted((params or {}).items())))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    tf = TeamForm(expected_from=expected_from, **(params or {}))
    tf.new_season(season)
    rows = conn.execute(
        "SELECT g.kickoff, g.home_team, g.away_team, g.home_score, g.away_score,"
        "       l.home_margin"
        "  FROM games g LEFT JOIN lines l ON l.game_id = g.game_id"
        " WHERE g.sport=? AND g.season=? AND g.home_score IS NOT NULL"
        "   AND g.away_score IS NOT NULL AND g.kickoff IS NOT NULL"
        " ORDER BY g.kickoff", (sport, season)).fetchall()
    for r in rows:
        if not settled_before(r["kickoff"], as_of):
            break                      # ordered by kickoff, so the rest are later
        expected = r["home_margin"] if expected_from == MARKET else None
        if expected is None:
            # NO EXPECTATION, NO UPDATE. A game with no line contributes nothing
            # rather than contributing its raw margin, which would silently make
            # form mean "how much did you win by" for exactly the games the
            # market did not price.
            continue
        tf.observe(home_team=r["home_team"], away_team=r["away_team"],
                   actual_home_margin=r["home_score"] - r["away_score"],
                   expected_home_margin=expected)
    _CACHE[key] = tf
    return tf


def clear_cache():
    _CACHE.clear()


# ── E005 as a challenger the registry can run ────────────────────────────────

from .base import ForecastModel                                   # noqa: E402

EXPERIMENT_ID = "E005"
FEATURE_SCHEMA = "champion_features_v2"
FEATURES = ["form_diff"]
LAMBDA_GRID = [0.01, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0]


class FormQuality(ForecastModel):
    """
    E005. The market number, adjusted by the difference in continuous form.

        pred_home_margin = consensus_spread + beta * form_diff

    WHAT IT IS TESTING. The Champion's quality term is a poll threshold: +5 for
    beating a top-5 side, +3 for a top-25 side, nothing for #26. This asks
    whether a continuous, decayed performance residual carries information the
    threshold throws away — measured, like every other challenger here, against
    the market rather than against the Champion alone.

    ONE COEFFICIENT, ON PURPOSE. There is one feature, so there is one number to
    fit, and a ridge over a single standardized column with an unpenalized
    intercept is the whole model. Anything more elaborate would be fitting the
    2025 season rather than the hypothesis.
    """

    model_id = "form-quality"
    feature_schema = FEATURE_SCHEMA
    experiment_id = EXPERIMENT_ID

    def __init__(self, artifact=None):
        self.art = artifact or {}

    @staticmethod
    def features_from_payload(payload):
        """The one feature, or None when it cannot be built honestly."""
        if payload.get("consensus_spread") is None:
            return None
        fd = payload.get("form_diff")
        if fd is None:
            return None
        return {"form_diff": float(fd)}

    def fit(self, rows, *, valid=None, lam=None, candidates=None):
        """
        Fit the one coefficient. `rows` carry `form_diff` and `residual`.

        Lambda is chosen on a split the fit never sees, exactly as E003 and E004
        choose theirs, so the three are comparable as fitting procedures and not
        only as hypotheses.
        """
        from .ridge import fit_ridge, choose_lambda
        usable = [r for r in rows if r.get("form_diff") is not None
                  and r.get("residual") is not None]
        if len(usable) < 50:
            raise ValueError("only %d usable row(s); refusing to fit a coefficient "
                             "on that" % len(usable))
        if lam is None:
            v = [r for r in (valid or []) if r.get("form_diff") is not None]
            if not v:
                raise ValueError(
                    "a validation split is required to choose lambda; choosing it "
                    "on the training rows is a longer way of writing zero")
            lam, scored = choose_lambda(usable, v, FEATURES, "residual",
                                        candidates or LAMBDA_GRID)
        else:
            scored = None
        art = fit_ridge(usable, FEATURES, "residual", lam)
        art.update({"model_id": self.model_id,
                    "experiment_id": self.experiment_id,
                    "feature_schema": self.feature_schema,
                    "target": "actual_minus_market_at_snapshot",
                    "expected_from": MARKET,
                    "market_timing_quality": MARKET_TIMING,
                    "form_params": TeamForm().state(),
                    "lambda_scored": scored})
        self.art = art
        return self

    def predict(self, payload):
        from .ridge import predict_ridge
        out = self.empty()
        feats = self.features_from_payload(payload)
        if feats is None or not self.art.get("coefficients"):
            return out
        resid = predict_ridge(self.art, feats)
        out["pred_home_margin"] = round(payload["consensus_spread"] + resid, 3)
        return out

    def artifact(self):
        return dict(self.art)
