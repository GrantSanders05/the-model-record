"""
market_baseline.py — C1. The number to beat.

    pred_home_margin = the market consensus at the model's own decision time

MANDATORY, and not a formality. The market is the best freely available forecast
of a football game, and a private model that cannot beat it on prediction error
has not demonstrated anything however good its ATS record looks over one season.
Every challenger's improvement is reported against this as well as against the
Champion, because "better than the Champion" and "better than the line" are
different claims and only the second is interesting to anyone else.

It reads the SAME feature payload as every other model, so the market number it
uses is the one that was on the screen at the forecast's own instant — not the
close, which would be a different and much easier baseline.
"""

from .base import ForecastModel


class MarketBaseline(ForecastModel):

    model_id = "market-baseline"
    feature_schema = "champion_features_v1"

    def fit(self, rows):
        return self                       # nothing to fit; that is the point

    def predict(self, payload):
        out = self.empty()
        out["pred_home_margin"] = payload.get("consensus_spread")
        out["pred_total"] = payload.get("consensus_total")
        # De-vigged consensus probability where BOTH moneyline sides existed.
        # None otherwise: halving a one-sided implied probability is a guess in
        # the clothes of a calculation.
        out["home_win_prob"] = payload.get("consensus_home_prob")
        return out

    def artifact(self):
        return {"model_id": self.model_id,
                "note": "the market consensus at the forecast's own decision time, "
                        "under the payload's market policy version"}
