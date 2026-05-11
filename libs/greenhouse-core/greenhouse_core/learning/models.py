"""Data models for the learning engine."""

from dataclasses import dataclass


@dataclass
class IrrigationResponse:
    """Soil moisture change for one sensor after one irrigation event."""

    sensor_id: int
    plant_id: int | None
    sensor_name: str
    event_id: int
    event_timestamp: int
    duration_minutes: int
    pre_moisture: float  # Soil moisture before irrigation
    post_moisture: float  # Soil moisture after irrigation (best reading in window)
    delta: float  # post - pre
    delta_per_minute: float  # delta / duration
    reading_delay_seconds: int  # Time between irrigation and post reading


@dataclass
class PlantProfile:
    """Learned irrigation profile for a plant/sensor."""

    sensor_id: int
    plant_id: int | None
    sensor_name: str
    avg_absorption_per_minute: float  # Average soil moisture increase per minute of irrigation
    avg_drainage_per_hour: float  # Average soil moisture decrease per hour (natural drying)
    response_count: int  # Number of irrigation events analyzed
    min_delta: float  # Worst response ever
    max_delta: float  # Best response ever
    efficiency_score: float  # 0-1: how well this plant responds to irrigation


@dataclass
class Alert:
    """System alert for operator attention."""

    severity: str  # "warning" or "critical"
    alert_type: str  # "blocked_drip", "unresolvable_conflict", "rapid_drainage", "chronic_underwatering"
    message: str
    sensor_name: str | None = None
    data: dict | None = None
