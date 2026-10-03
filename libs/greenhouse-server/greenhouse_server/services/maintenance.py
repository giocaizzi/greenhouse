"""Maintenance and learning alert collection."""

import logging
import statistics
import time
from typing import TYPE_CHECKING, Any

from greenhouse_core.constants import (
    LOW_LIGHT_ALERT_FRACTION,
    MAINTENANCE_HUMIDITY_DEFICIT,
    MAINTENANCE_LOOKBACK_HOURS,
    MAINTENANCE_MIN_SAMPLES,
    MAINTENANCE_STALE_SECONDS,
    SECONDS_PER_HOUR,
)
from greenhouse_core.learning import IrrigationLearner
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.utils import daytime_lux_readings, effective_light_threshold

if TYPE_CHECKING:
    from greenhouse_core.models import Plant, Sensor, SensorReading

logger = logging.getLogger(__name__)


def collect_learning_alerts(
    repo: IrrigationRepository, cluster_id: int, plant_db: PlantDatabase
) -> list[dict[str, Any]]:
    """Return learning alerts for a cluster (efficiency, patterns). Never raises."""
    try:
        learner = IrrigationLearner(repo, plant_db)
        issues = learner.detect_issues(cluster_id)
        return [{"severity": a.severity, "type": a.alert_type, "message": a.message} for a in issues]
    except Exception:
        logger.debug("Learning alerts unavailable for cluster %d", cluster_id, exc_info=True)
        return []


def _battery_alert(sensor: "Sensor", readings: "list[SensorReading]") -> dict[str, Any] | None:
    """Warn when the newest reported battery state is low."""
    latest_bat = next((r.battery_state for r in readings if r.battery_state is not None), None)
    if latest_bat == "low":
        return {
            "severity": "warning",
            "type": "battery_low",
            "message": f"{sensor.name}: battery low",
        }
    return None


def _stale_alert(sensor: "Sensor", readings: "list[SensorReading]", now: int) -> dict[str, Any] | None:
    """Warn when the sensor has not reported recently (or never)."""
    latest_ts = readings[0].timestamp if readings else None
    if latest_ts is None or (now - latest_ts) > MAINTENANCE_STALE_SECONDS:
        age_h = (now - latest_ts) / SECONDS_PER_HOUR if latest_ts else None
        age_str = f"{age_h:.0f}h ago" if age_h else "never"
        return {
            "severity": "warning",
            "type": "stale_data",
            "message": f"{sensor.name}: no recent data (last: {age_str})",
        }
    return None


def _humidity_alert(
    sensor: "Sensor", readings: "list[SensorReading]", plant: "Plant | None", plant_db: PlantDatabase
) -> dict[str, Any] | None:
    """Warn when ambient humidity sits well below the plant's ideal minimum."""
    hum_vals = [r.env_humidity for r in readings if r.env_humidity is not None]
    if len(hum_vals) >= MAINTENANCE_MIN_SAMPLES:
        avg_env_hum = statistics.mean(hum_vals)
        if plant:
            care = plant_db.get_care_data(species=plant.species, category=plant.category)
            ideal_hum_min = care.get("ideal_humidity_min")
            if ideal_hum_min and avg_env_hum < ideal_hum_min - MAINTENANCE_HUMIDITY_DEFICIT:
                return {
                    "severity": "warning",
                    "type": "low_env_humidity",
                    "message": f"{sensor.name}: humidity {avg_env_hum:.0f}% (ideal >={ideal_hum_min:.0f}%)",
                }
    return None


def _light_alert(
    sensor: "Sensor", readings: "list[SensorReading]", plant: "Plant | None", plant_db: PlantDatabase
) -> dict[str, Any] | None:
    """Warn when daytime light stays well below the plant's seasonal minimum."""
    lux_vals = daytime_lux_readings(readings)
    if len(lux_vals) < MAINTENANCE_MIN_SAMPLES:
        return None
    avg_lux = statistics.mean(lux_vals)
    if not plant:
        return None
    care = plant_db.get_care_data(species=plant.species, category=plant.category)
    min_lux = care.get("ideal_light_lux_min")
    if min_lux:
        seasonal_min = effective_light_threshold(min_lux)
        if avg_lux < seasonal_min * LOW_LIGHT_ALERT_FRACTION:
            return {
                "severity": "warning",
                "type": "low_light",
                "message": f"{sensor.name}: avg {avg_lux:.0f} lux (seasonal min {seasonal_min:.0f})",
            }
    return None


def collect_maintenance_alerts(
    repo: IrrigationRepository, cluster_id: int, plant_db: PlantDatabase
) -> list[dict[str, Any]]:
    """Return maintenance alerts (hardware, environment). Never raises."""
    alerts: list[dict[str, Any]] = []
    sensors = repo.get_sensors_in_cluster(cluster_id)
    plants_by_id = {p.id: p for p in repo.get_plants_in_cluster(cluster_id)}
    now = int(time.time())

    for sensor in sensors:
        readings = repo.get_recent_readings(sensor.id, hours=MAINTENANCE_LOOKBACK_HOURS)
        plant = plants_by_id.get(sensor.plant_id) if sensor.plant_id else None
        # Per-sensor order: battery, stale, humidity, light.
        alerts.extend(
            alert
            for alert in (
                _battery_alert(sensor, readings),
                _stale_alert(sensor, readings, now),
                _humidity_alert(sensor, readings, plant, plant_db),
                _light_alert(sensor, readings, plant, plant_db),
            )
            if alert is not None
        )

    return alerts


def generate_learning_report(repo: IrrigationRepository, cluster_id: int, plant_db: PlantDatabase) -> str:
    """Generate a full learning report for a cluster."""
    learner = IrrigationLearner(repo, plant_db)
    return learner.generate_report(cluster_id)
