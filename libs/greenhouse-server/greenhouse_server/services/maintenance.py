"""Maintenance and learning alert collection."""

import statistics
import time

from greenhouse_core.learning import IrrigationLearner
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.utils import daytime_lux_readings, effective_light_threshold


def collect_learning_alerts(repo: IrrigationRepository, cluster_id: int, plant_db: PlantDatabase) -> list[dict]:
    """Return learning alerts for a cluster (efficiency, patterns). Never raises."""
    try:
        learner = IrrigationLearner(repo, plant_db)
        issues = learner.detect_issues(cluster_id)
        return [{"severity": a.severity, "type": a.alert_type, "message": a.message} for a in issues]
    except Exception:
        return []


def collect_maintenance_alerts(repo: IrrigationRepository, cluster_id: int, plant_db: PlantDatabase) -> list[dict]:
    """Return maintenance alerts (hardware, environment). Never raises."""
    alerts = []
    sensors = repo.get_sensors_in_cluster(cluster_id)
    plants_by_id = {p.id: p for p in repo.get_plants_in_cluster(cluster_id)}
    now = int(time.time())

    for sensor in sensors:
        readings = repo.get_recent_readings(sensor.id, hours=24)

        # Battery low
        latest_bat = next((r.battery_state for r in readings if r.battery_state is not None), None)
        if latest_bat == "low":
            alerts.append(
                {
                    "severity": "warning",
                    "type": "battery_low",
                    "message": f"{sensor.name}: battery low",
                }
            )

        # Stale data (no readings in 3h)
        latest_ts = readings[0].timestamp if readings else None
        if latest_ts is None or (now - latest_ts) > 3 * 3600:
            age_h = (now - latest_ts) / 3600 if latest_ts else None
            age_str = f"{age_h:.0f}h ago" if age_h else "never"
            alerts.append(
                {
                    "severity": "warning",
                    "type": "stale_data",
                    "message": f"{sensor.name}: no recent data (last: {age_str})",
                }
            )

        # Low ambient humidity
        hum_vals = [r.env_humidity for r in readings if r.env_humidity is not None]
        if len(hum_vals) >= 3:
            avg_env_hum = statistics.mean(hum_vals)
            plant = plants_by_id.get(sensor.plant_id) if sensor.plant_id else None
            if plant:
                care = plant_db.get_care_data(species=plant.species, category=plant.category)
                ideal_hum_min = care.get("ideal_humidity_min")
                if ideal_hum_min and avg_env_hum < ideal_hum_min - 10:
                    alerts.append(
                        {
                            "severity": "warning",
                            "type": "low_env_humidity",
                            "message": f"{sensor.name}: humidity {avg_env_hum:.0f}% (ideal >={ideal_hum_min:.0f}%)",
                        }
                    )

        # Low light (daytime, seasonal)
        lux_vals = daytime_lux_readings(readings)
        if len(lux_vals) >= 3:
            avg_lux = statistics.mean(lux_vals)
            plant = plants_by_id.get(sensor.plant_id) if sensor.plant_id else None
            if plant:
                care = plant_db.get_care_data(species=plant.species, category=plant.category)
                min_lux = care.get("ideal_light_lux_min")
                if min_lux:
                    seasonal_min = effective_light_threshold(min_lux)
                    if avg_lux < seasonal_min * 0.5:
                        alerts.append(
                            {
                                "severity": "warning",
                                "type": "low_light",
                                "message": f"{sensor.name}: avg {avg_lux:.0f} lux (seasonal min {seasonal_min:.0f})",
                            }
                        )

    return alerts


def generate_learning_report(repo: IrrigationRepository, cluster_id: int, plant_db: PlantDatabase) -> str:
    """Generate a full learning report for a cluster."""
    learner = IrrigationLearner(repo, plant_db)
    return learner.generate_report(cluster_id)
