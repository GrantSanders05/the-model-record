"""
matchup_residual.py — C3 / E004. Position groups meet each other, not an average.

C2 reduces a rich grade sheet to differences of like against like: our line minus
their line. That is not how a football game works. An offensive line is graded
against the defensive line it will face; a secondary matters more against a team
that throws.

    home_ol_vs_away_dl = home OL - away DL
    away_ol_vs_home_dl = away OL - home DL
    pass game          = (QB + WR) against (DB + DL)
    run game           = (RB + OL) against (DL + LB)

THE FORMULAS ARE PRE-REGISTERED, and that is the entire discipline of this file.
With eight positions there are dozens of plausible interactions and a season of
development data will happily rank one of them best; picking the winner after
looking is how a model acquires a beautiful backtest and no future. These six are
written down first because they are how coaches talk about a matchup, and they
are then fitted under the same ridge as C2 rather than selected.

Built on C2's features rather than instead of them, so a comparison between the
two is a question about the interactions alone.
"""

from .base import ForecastModel
from .residual_grade import POSITIONS, features_from_payload as _base_features
from .ridge import fit_ridge, predict_ridge, choose_lambda

FEATURE_SCHEMA = "matchup_grade_v1"
EXPERIMENT_ID = "E004"

MATCHUP_FEATURES = [
    "home_ol_vs_away_dl", "away_ol_vs_home_dl",
    "home_pass_vs_away_cover", "away_pass_vs_home_cover",
    "home_run_vs_away_box", "away_run_vs_home_box",
    "coach_st_diff_m",
]
FEATURES = ["total_grade_diff", "market_spread", "neutral_site"] + MATCHUP_FEATURES
LAMBDA_GRID = [0.01, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0]


def features_from_payload(payload):
    """C2's inputs plus the pre-registered matchups. -> dict | None"""
    base = _base_features(payload)
    if base is None:
        return None
    h = payload["home_grade_vector"]
    a = payload["away_grade_vector"]

    # Equal weights inside each pairing. A weight is a parameter, and a parameter
    # chosen by hand is a parameter fitted on the whole sample by eye.
    def pair(x, y):
        return (x + y) / 2.0

    row = {
        "total_grade_diff": base["total_grade_diff"],
        "market_spread": base["market_spread"],
        "neutral_site": base["neutral_site"],
        "home_ol_vs_away_dl": h["ol"] - a["dl"],
        "away_ol_vs_home_dl": a["ol"] - h["dl"],
        "home_pass_vs_away_cover": pair(h["qb"], h["wr"]) - pair(a["db"], a["dl"]),
        "away_pass_vs_home_cover": pair(a["qb"], a["wr"]) - pair(h["db"], h["dl"]),
        "home_run_vs_away_box": pair(h["rb"], h["ol"]) - pair(a["dl"], a["lb"]),
        "away_run_vs_home_box": pair(a["rb"], a["ol"]) - pair(h["dl"], h["lb"]),
        "coach_st_diff_m": h["coach_st"] - a["coach_st"],
    }
    return row


class MatchupResidual(ForecastModel):

    model_id = "matchup-residual"
    feature_schema = FEATURE_SCHEMA
    experiment_id = EXPERIMENT_ID

    def __init__(self, artifact=None):
        self._artifact = artifact

    def fit(self, rows, *, valid=None, lam=None, candidates=None):
        if lam is None:
            if not valid:
                raise ValueError("a validation split is required to choose lambda")
            lam, scored = choose_lambda(rows, valid, FEATURES, "residual",
                                        candidates or LAMBDA_GRID)
        else:
            scored = None
        art = fit_ridge(rows, FEATURES, "residual", lam)
        art.update({"model_id": self.model_id, "experiment_id": EXPERIMENT_ID,
                    "feature_schema": FEATURE_SCHEMA,
                    "target": "actual_minus_market_at_snapshot",
                    "lambda_scored": scored})
        self._artifact = art
        return self

    def predict(self, payload):
        out = self.empty()
        if self._artifact is None:
            return out
        row = features_from_payload(payload)
        if row is None:
            out["borrowed_fallback"] = 1
            return out
        out["pred_home_margin"] = payload["consensus_spread"] + predict_ridge(
            self._artifact, row)
        return out

    def artifact(self):
        return dict(self._artifact or {})
