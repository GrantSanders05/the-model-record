"""
residual_grade.py — C2 / E003. What do the grades know that the market has not priced?

    target = actual_home_margin - market_home_margin AT THE FORECAST'S OWN TIME

THE TARGET IS THE HYPOTHESIS. Predicting the margin asks "can these grades
forecast a football game", and the answer is mostly "yes, and so can the line".
Predicting the market RESIDUAL asks the only question worth money: what does a
human watching film know that a market of people with money has not already put
in the number.

It also removes almost everything the grades cannot see. Home field, travel,
rest, injuries, weather and public money are all in the market number already,
so a residual model does not have to model them to avoid being wrong about them.

WHY THE FEATURES ARE FEW AND FLAT. Eight position differences, the market number,
and neutral site. Not because more would not help, but because 800 development
games will fit any number of features beautifully and none of them out of sample.
The interactions live in C3, registered separately, so that if it works there is
something to point at.

TWO TRAPS, BOTH AVOIDED HERE AND BOTH WORTH NAMING:

  HOME FIELD IS ALREADY IN THE TARGET. The market's number contains the market's
  home-field expectation, so a home-field term here is not "how much home field
  is worth", it is "how much the market misprices home field". `neutral_site` is
  included on exactly that reading and nothing else about the venue is.

  THE DEVELOPMENT MARKET NUMBER IS NOT A T2 SNAPSHOT. 2025's stored line is one
  preferred provider at an unrecorded time. Fitting on that and forecasting at T2
  is a change of data regime, and rows carry `market_timing_quality` saying which
  they are. That does not make the fit useless; it makes the limitation visible
  instead of arriving later as a surprise.
"""

from .base import ForecastModel
from .ridge import fit_ridge, predict_ridge, choose_lambda

POSITIONS = ["qb", "rb", "wr", "ol", "dl", "lb", "db", "coach_st"]

FEATURES = ["total_grade_diff"] + ["%s_diff" % p for p in POSITIONS] + \
           ["market_spread", "neutral_site"]

FEATURE_SCHEMA = "residual_grade_v1"
EXPERIMENT_ID = "E003"

# Fixed before any prospective result is looked at, and then left alone.
LAMBDA_GRID = [0.01, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0]


def features_from_payload(payload):
    """
    Turn a feature snapshot into model inputs. -> dict | None

    None when either team has no grade vector. A model that cannot see one side
    of a game has no opinion about it, and filling the gap with zeros would make
    "no information" look like "average", which is a different and much more
    confident claim.
    """
    h = payload.get("home_grade_vector")
    a = payload.get("away_grade_vector")
    mkt = payload.get("consensus_spread")
    if not h or not a or mkt is None:
        return None
    if any(h.get(p) is None or a.get(p) is None for p in POSITIONS):
        return None
    row = {"%s_diff" % p: h[p] - a[p] for p in POSITIONS}
    row["total_grade_diff"] = sum(h[p] - a[p] for p in POSITIONS)
    row["market_spread"] = mkt
    row["neutral_site"] = float(payload.get("neutral_site") or 0)
    return row


class ResidualGrade(ForecastModel):

    model_id = "residual-grade"
    feature_schema = FEATURE_SCHEMA
    experiment_id = EXPERIMENT_ID

    def __init__(self, artifact=None):
        self._artifact = artifact

    def fit(self, rows, *, valid=None, lam=None, candidates=None):
        """
        Fit on development rows. `rows` carry the features plus `residual`.

        Lambda is chosen on `valid` — a split the fit never sees — or supplied
        explicitly. It is never chosen on the training rows, and never on a
        prospective season.
        """
        if lam is None:
            if not valid:
                raise ValueError(
                    "a validation split is required to choose lambda; choosing it "
                    "on the training rows is a longer way of writing zero")
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
        residual = predict_ridge(self._artifact, row)
        out["pred_home_margin"] = payload["consensus_spread"] + residual
        out["margin_uncertainty"] = self._artifact.get("residual_sd")
        return out

    def artifact(self):
        return dict(self._artifact or {})
