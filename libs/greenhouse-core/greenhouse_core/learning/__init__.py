"""Irrigation learning engine — profiling, issue detection, reporting.

The re-exports resolve lazily on first attribute access (see
:mod:`greenhouse_core._lazy`), so importing the light ``learning.models``
does not load the learner, the issue heuristics and the decision engine.
"""

from typing import TYPE_CHECKING

from greenhouse_core._lazy import lazy_module_attrs

if TYPE_CHECKING:
    from greenhouse_core.learning.learner import IrrigationLearner
    from greenhouse_core.learning.models import Alert, IrrigationResponse, PlantProfile

_MODELS = "greenhouse_core.learning.models"

# Re-exported name -> the module that defines it.
_EXPORTS: dict[str, str] = {
    "Alert": _MODELS,
    "IrrigationLearner": "greenhouse_core.learning.learner",
    "IrrigationResponse": _MODELS,
    "PlantProfile": _MODELS,
}

# Submodules, listed by ``dir()`` as they were when the package imported them eagerly.
_MODULES: dict[str, str] = {
    name: f"{__name__}.{name}" for name in ("issues", "learner", "models", "profiling", "report")
}

__all__ = ["Alert", "IrrigationLearner", "IrrigationResponse", "PlantProfile"]

if not TYPE_CHECKING:  # hidden from mypy, so a misspelt attribute stays a type error
    __getattr__, __dir__ = lazy_module_attrs(__name__, globals(), _EXPORTS, _MODULES)
