"""Data models for irrigation system."""

from dataclasses import dataclass


@dataclass
class Cluster:
    """A cluster of plants irrigated by the same device."""

    id: int | None
    name: str
    location: str | None
    created_at: int


@dataclass
class Plant:
    """A plant in a cluster."""

    id: int | None
    cluster_id: int
    species: str  # e.g., "Monstera deliciosa"
    category: str | None  # e.g., "tropical", "succulent"
    water_needs: str | None  # "low", "medium", "high"
    light_needs: str | None  # "low", "medium", "high"
    ideal_temp_min: float | None  # °C
    ideal_temp_max: float | None  # °C
    ideal_humidity_min: float | None  # %
    ideal_humidity_max: float | None  # %
    notes: str | None


@dataclass
class Irrigator:
    """An irrigation device (Tuya-based)."""

    id: int | None
    cluster_id: int
    tuya_device_id: str
    name: str
    type: str  # "tuya_cloud", "tuya_local"
    config: str  # JSON string


@dataclass
class Sensor:
    """A sensor device (Tuya-based)."""

    id: int | None
    cluster_id: int
    tuya_device_id: str
    name: str
    type: str  # "temp_humidity", "soil_moisture", "light"
    config: str  # JSON string


@dataclass
class SensorReading:
    """A sensor reading."""

    id: int | None
    sensor_id: int
    timestamp: int
    temperature: float | None
    humidity: float | None
    soil_moisture: float | None
    light: int | None


@dataclass
class IrrigationEvent:
    """An irrigation event (start, stop, etc.)."""

    id: int | None
    irrigator_id: int
    timestamp: int
    action: str  # "start", "stop", "schedule_updated"
    duration_minutes: int | None
    triggered_by: str  # "manual", "auto", "schedule"
    notes: str | None


@dataclass
class IrrigationConfig:
    """Irrigation configuration for a cluster."""

    id: int | None
    cluster_id: int
    mode: str  # "manual", "schedule", "smart"
    duration_minutes: int | None
    interval_hours: int | None
    auto_run: bool
    last_updated: int
