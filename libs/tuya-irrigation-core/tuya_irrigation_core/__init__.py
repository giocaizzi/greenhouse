"""Core library for tuya-irrigation: models, repository, business logic."""

from tuya_irrigation_core.cloud import TuyaCloud
from tuya_irrigation_core.database import create_db_engine, create_session_factory, init_db
from tuya_irrigation_core.devices import TuyaDeviceManager
from tuya_irrigation_core.learning import IrrigationLearner
from tuya_irrigation_core.logic import IrrigationLogic
from tuya_irrigation_core.models import (
    Cluster,
    IrrigationConfig,
    IrrigationEvent,
    Irrigator,
    Plant,
    Sensor,
    SensorReading,
)
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository

__all__ = [
    "Cluster",
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
    "TuyaCloud",
    "TuyaDeviceManager",
    "create_db_engine",
    "create_session_factory",
    "init_db",
]
