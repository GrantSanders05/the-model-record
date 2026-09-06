"""base.py — the interface every V2 model answers to."""


class ForecastModel:
    """
    One prediction from one feature payload.

    `model_id` names the family; a VERSION is assigned by the registry and carries
    the config hash, so two fits of the same family are two versions.
    """

    model_id = None
    feature_schema = None

    def fit(self, rows):
        """Train from development rows. Champion-style models need no fit."""
        raise NotImplementedError

    def predict(self, payload):
        """
        -> dict with the normalized keys. Unsupported fields are None.

            pred_home_margin, pred_total, home_win_prob, home_cover_prob,
            over_prob, margin_uncertainty, total_uncertainty, borrowed_fallback
        """
        raise NotImplementedError

    def artifact(self):
        """Serializable fitted parameters and metadata, for the registry."""
        return {}

    @classmethod
    def is_fitted(cls, artifact):
        """
        Does this artifact carry everything the model needs to predict? -> bool

        Asked of the CLASS rather than guessed by the loader. A loader that looks
        for one well-known key silently drops every model shaped differently —
        C6 keeps two sub-artifacts and no top-level coefficients, and would have
        been skipped on every run with nothing but a log line to say so.
        """
        return bool((artifact or {}).get("coefficients"))

    @staticmethod
    def empty():
        return {"pred_home_margin": None, "pred_total": None,
                "home_win_prob": None, "home_cover_prob": None, "over_prob": None,
                "margin_uncertainty": None, "total_uncertainty": None,
                "borrowed_fallback": 0}
