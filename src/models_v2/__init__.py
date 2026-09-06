"""
models_v2 — the Champion's challengers, all behind one interface.

Every model here returns the same normalized dict, so `forecast_v2` can run any
of them beside the Champion without knowing what is inside. A field a model does
not support is None, never a default: a model with no calibrated cover
probability must say so rather than emit 0.5.

NOTHING HERE IS PROMOTED AUTOMATICALLY. A challenger produces shadow forecasts
and accumulates a prospective record; whether it becomes Champion is a decision
with a checklist behind it (§30), not a threshold.
"""

from .base import ForecastModel                 # noqa: F401
from .market_baseline import MarketBaseline     # noqa: F401
from .ridge import Ridge, fit_ridge             # noqa: F401
from .residual_grade import ResidualGrade       # noqa: F401
from .matchup_residual import MatchupResidual   # noqa: F401
