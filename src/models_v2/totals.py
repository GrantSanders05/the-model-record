"""
totals.py — C6. A total is not a spread, and this does not pretend otherwise.

§20.8 is explicit: DO NOT REUSE SPREAD LOGIC. A spread is a statement about
relative strength; a total is a statement about how many possessions two teams
will have and what they will do with them. The spread challengers here all
predict the market's residual, which works because the market spread already
carries almost everything. Doing the same for totals would produce a model whose
only content is "the total, plus a small correction" — and would tell nobody
whether the scoring architecture is any good.

So this predicts BOTH TEAM SCORES SEPARATELY, from scoring rates, and adds them:

    home_points ~ f(home offence to date, away defence to date, venue)
    away_points ~ f(away offence to date, home defence to date, venue)
    pred_total   = home_points + away_points

Two fits, not one, because a home offence facing a road defence is a different
question from the mirror of it, and the home-field term is not symmetric.

WHAT IT IS NOT. It has no pace term, no efficiency term, no weather and no
availability. §20.8 lists all of those as inputs LATER. Reporting this as
'the totals model' would be the overclaim; it is the first architecture, it runs
shadow, and the strategy keeps `totals_enabled: False` until a totals model has
been validated on its own evidence rather than on a spread model's reputation.
"""

from .base import ForecastModel
from .ridge import fit_ridge, predict_ridge, choose_lambda

EXPERIMENT_ID = "E006"
FEATURE_SCHEMA = "champion_features_v3"
LAMBDA_GRID = [0.01, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0]

HOME_FEATURES = ["off_pg", "opp_def_pg", "neutral"]
AWAY_FEATURES = ["off_pg", "opp_def_pg", "neutral"]

# A game enters a team's scoring rates only once it had certainly finished, for
# the same reason form uses it: a game kicked off two hours ago is still being
# played. Shared with form_quality so there is one settle rule, not two.
SETTLE_HOURS = 6
SHRINK_GAMES = 3.0        # early-season rates are shrunk toward the league mean


class TeamScoring:
    """
    Points scored and allowed per game, to date, shrunk toward the league mean.

    Two games at 45 points is not a 45-point offence. The shrink is n/(n+k)
    toward the league's own mean over the same games, so week 1 reports the
    league and week 12 reports the team, with no threshold in between and no
    'not enough data' state to design around.
    """

    def __init__(self, *, shrink_games=SHRINK_GAMES):
        self.shrink_games = shrink_games
        self.pf = {}
        self.pa = {}
        self.n = {}
        self._pts = 0.0
        self._games = 0

    def league_mean(self):
        """Mean points by one team in one game. 24.0 before anything is known."""
        return (self._pts / self._games) if self._games else 24.0

    def _rate(self, table, team):
        n = self.n.get(team, 0)
        lm = self.league_mean()
        if not n:
            return lm
        raw = table.get(team, 0.0) / n
        w = n / (n + self.shrink_games)
        return w * raw + (1 - w) * lm

    def offence(self, team):
        return self._rate(self.pf, team)

    def defence(self, team):
        return self._rate(self.pa, team)

    def games(self, team):
        return self.n.get(team, 0)

    def observe(self, *, home_team, away_team, home_score, away_score):
        if home_score is None or away_score is None:
            return
        for team, pf, pa in ((home_team, home_score, away_score),
                             (away_team, away_score, home_score)):
            self.pf[team] = self.pf.get(team, 0.0) + pf
            self.pa[team] = self.pa.get(team, 0.0) + pa
            self.n[team] = self.n.get(team, 0) + 1
        self._pts += home_score + away_score
        self._games += 2

    def state(self):
        return {"shrink_games": self.shrink_games, "teams": len(self.n),
                "league_mean": round(self.league_mean(), 3),
                "team_games": self._games // 2}


_CACHE = {}


def scoring_asof(conn, sport, season, as_of, *, shrink_games=SHRINK_GAMES):
    """Scoring rates as they stood at `as_of`, replayed from finished games."""
    from .form_quality import settled_before
    key = (id(conn), sport, season, as_of, shrink_games)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    ts = TeamScoring(shrink_games=shrink_games)
    for r in conn.execute(
            "SELECT kickoff, home_team, away_team, home_score, away_score"
            "  FROM games WHERE sport=? AND season=? AND home_score IS NOT NULL"
            "   AND away_score IS NOT NULL AND kickoff IS NOT NULL"
            " ORDER BY kickoff", (sport, season)):
        if not settled_before(r["kickoff"], as_of):
            break
        ts.observe(home_team=r["home_team"], away_team=r["away_team"],
                   home_score=r["home_score"], away_score=r["away_score"])
    _CACHE[key] = ts
    return ts


def clear_cache():
    _CACHE.clear()


def sides_from_payload(payload):
    """
    The two rows this model fits on, or None. -> (home_row, away_row)

    None when the scoring rates are absent, which is what a payload recorded
    under an older schema looks like. Reading a missing rate as the league mean
    would make every such game a league-average game and hide the gap.
    """
    hs, as_ = payload.get("home_scoring"), payload.get("away_scoring")
    if not hs or not as_:
        return None
    if hs.get("off_pg") is None or as_.get("off_pg") is None:
        return None
    neutral = float(payload.get("neutral_site") or 0)
    return ({"off_pg": float(hs["off_pg"]), "opp_def_pg": float(as_["def_pg"]),
             "neutral": neutral},
            {"off_pg": float(as_["off_pg"]), "opp_def_pg": float(hs["def_pg"]),
             "neutral": neutral})


class TotalsScoring(ForecastModel):
    """C6. Two side models, added. Shadow only."""

    model_id = "totals-scoring"
    feature_schema = FEATURE_SCHEMA
    experiment_id = EXPERIMENT_ID

    def __init__(self, artifact=None):
        self.art = artifact or {}

    def fit(self, rows, *, valid=None, candidates=None):
        """
        `rows` carry home_row/away_row features plus `home_points`/`away_points`.

        Two fits. The home side and the away side are not the same question — a
        home offence and a road offence differ by more than a constant — and
        fitting one model to both would bury that in an intercept.
        """
        usable = [r for r in rows
                  if r.get("home_points") is not None and r.get("off_pg_home") is not None]
        if len(usable) < 100:
            raise ValueError("only %d usable row(s); refusing to fit a totals "
                             "model on that" % len(usable))
        v = [r for r in (valid or []) if r.get("off_pg_home") is not None]
        if not v:
            raise ValueError("a validation split is required to choose lambda")

        art = {"model_id": self.model_id, "experiment_id": self.experiment_id,
               "feature_schema": self.feature_schema,
               "target": "team_points_from_scoring_rates",
               "architecture": "two side models, summed; no pace, efficiency, "
                               "weather or availability term",
               "scoring_params": TeamScoring().state()}
        def _row(r, cols, side, target):
            out = {c: r["%s_%s" % (c, side)] for c in cols}
            out[target] = r.get(target)
            return out

        for side, cols, target in (("home", HOME_FEATURES, "home_points"),
                                   ("away", AWAY_FEATURES, "away_points")):
            tr = [_row(r, cols, side, target) for r in usable]
            va = [_row(r, cols, side, target) for r in v
                  if r.get(target) is not None]
            lam, scored = choose_lambda(tr, va, cols, target,
                                        candidates or LAMBDA_GRID)
            sub = fit_ridge(tr, cols, target, lam)
            sub["lambda_scored"] = scored
            art[side] = sub
        self.art = art
        return self

    def predict(self, payload):
        out = self.empty()
        sides = sides_from_payload(payload)
        if sides is None or not self.art.get("home") or not self.art.get("away"):
            return out
        home_row, away_row = sides
        hp = predict_ridge(self.art["home"], home_row)
        ap = predict_ridge(self.art["away"], away_row)
        out["pred_total"] = round(hp + ap, 3)
        # NO SPREAD. This model has no opinion about who wins, and emitting the
        # difference of two shrunk scoring rates as a margin would be a number
        # with a plausible shape and no thought behind it.
        return out

    @classmethod
    def is_fitted(cls, artifact):
        a = artifact or {}
        return bool(a.get("home", {}).get("coefficients")
                    and a.get("away", {}).get("coefficients"))

    def artifact(self):
        return dict(self.art)
