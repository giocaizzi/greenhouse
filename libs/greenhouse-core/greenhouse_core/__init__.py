"""Core library for greenhouse: models, repository, business logic.

The convenience re-exports resolve lazily, on first attribute access (see
:mod:`greenhouse_core._lazy`): importing a leaf module such as
``greenhouse_core.constants`` must not drag in SQLAlchemy, Alembic, the
decision engine and the device stack just because Python initialises this
package first. ``from greenhouse_core import IrrigationLogic`` behaves exactly
as before; the type checker sees the real imports in the ``TYPE_CHECKING``
block.
"""

from typing import TYPE_CHECKING

from greenhouse_core._lazy import lazy_module_attrs

if TYPE_CHECKING:
    from greenhouse_core.database import create_db_engine, create_session_factory, init_db
    from greenhouse_core.devices import DeviceGateway, DeviceRegistry, build_default_registry
    from greenhouse_core.learning import IrrigationLearner
    from greenhouse_core.logic import IrrigationLogic
    from greenhouse_core.models import (
        Cluster,
        IrrigationConfig,
        IrrigationEvent,
        Irrigator,
        Plant,
        Sensor,
        SensorReading,
    )
    from greenhouse_core.plant_db import PlantDatabase
    from greenhouse_core.repository import IrrigationRepository

_MODELS = "greenhouse_core.models"
_DEVICES = "greenhouse_core.devices"
_DATABASE = "greenhouse_core.database"

# Re-exported name -> the module that defines it.
_EXPORTS: dict[str, str] = {
    "Cluster": _MODELS,
    "DeviceGateway": _DEVICES,
    "DeviceRegistry": _DEVICES,
    "IrrigationConfig": _MODELS,
    "IrrigationEvent": _MODELS,
    "IrrigationLearner": "greenhouse_core.learning",
    "IrrigationLogic": "greenhouse_core.logic",
    "IrrigationRepository": "greenhouse_core.repository",
    "Irrigator": _MODELS,
    "Plant": _MODELS,
    "PlantDatabase": "greenhouse_core.plant_db",
    "Sensor": _MODELS,
    "SensorReading": _MODELS,
    "build_default_registry": _DEVICES,
    "create_db_engine": _DATABASE,
    "create_session_factory": _DATABASE,
    "init_db": _DATABASE,
}

__all__ = [
    "Cluster",
    "DeviceGateway",
    "DeviceRegistry",
    "IrrigationConfig",
    "IrrigationEvent",
    "IrrigationLearner",
    "IrrigationLogic",
    "IrrigationRepository",
    "Irrigator",
    "Plant",
    "PlantDatabase",
    "Sensor",
    "SensorReading",
    "build_default_registry",
    "create_db_engine",
    "create_session_factory",
    "init_db",
]

if not TYPE_CHECKING:  # hidden from mypy, so a misspelt attribute stays a type error
    __getattr__, __dir__ = lazy_module_attrs(__name__, globals(), _EXPORTS, {})
