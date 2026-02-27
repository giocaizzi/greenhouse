"""Tuya Irrigation - Smart plant care system with evidence-based data and trend analysis."""

from tuya_irrigation.db import IrrigationDB
from tuya_irrigation.devices import TuyaDeviceManager
from tuya_irrigation.logic import IrrigationLogic
from tuya_irrigation.models import Cluster, IrrigationConfig, Irrigator, Plant, Sensor, SensorReading
from tuya_irrigation.plant_db import get_plant_database

__version__ = "0.2.0"
__all__ = [
    "IrrigationDB",
    "TuyaDeviceManager",
    "IrrigationLogic",
    "Cluster",
    "IrrigationConfig",
    "Irrigator",
    "Plant",
    "Sensor",
    "SensorReading",
    "get_plant_database",
]
