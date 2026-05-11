"""Sensor data aggregation for irrigation decisions."""

import statistics

from greenhouse_core.logic.decision import PerSensorSnapshot, SensorSnapshot
from greenhouse_core.repository import IrrigationRepository


def get_recent_sensor_data(db: IrrigationRepository, cluster_id: int, hours: int = 24) -> SensorSnapshot:
    """Build a typed snapshot of cluster sensors over the lookback window.

    The snapshot drives every downstream rule (stress, conflict, adjustments)
    and is persisted with the decision so the audit log can replay the inputs.
    """
    sensors = db.get_sensors_in_cluster(cluster_id)
    if not sensors:
        return SensorSnapshot()

    all_temps: list[float] = []
    all_env_humidity: list[float] = []
    all_soil: list[float] = []
    all_light: list[int] = []
    water_warnings: list[str] = []
    per_sensor: list[PerSensorSnapshot] = []

    for sensor in sensors:
        readings = db.get_recent_readings(sensor.id, hours=hours)
        s_temps: list[float] = []
        s_humidity: list[float] = []
        s_soil: list[float] = []

        for r in readings:
            if r.temperature is not None:
                all_temps.append(r.temperature)
                s_temps.append(r.temperature)
            if r.env_humidity is not None:
                all_env_humidity.append(r.env_humidity)
                s_humidity.append(r.env_humidity)
            if r.soil_moisture is not None:
                all_soil.append(r.soil_moisture)
                s_soil.append(r.soil_moisture)
            if r.light is not None and r.light > 15:
                all_light.append(r.light)
            if r.water_warning:
                water_warnings.append(sensor.name)

        per_sensor.append(
            PerSensorSnapshot(
                sensor_id=sensor.id,
                plant_id=sensor.plant_id,
                name=sensor.name,
                avg_temperature=statistics.mean(s_temps) if s_temps else None,
                avg_humidity=statistics.mean(s_humidity) if s_humidity else None,
                avg_soil_moisture=statistics.mean(s_soil) if s_soil else None,
            )
        )

    return SensorSnapshot(
        avg_temperature=statistics.mean(all_temps) if all_temps else None,
        avg_env_humidity=statistics.mean(all_env_humidity) if all_env_humidity else None,
        avg_soil_moisture=statistics.mean(all_soil) if all_soil else None,
        min_soil_moisture=min(all_soil) if all_soil else None,
        max_soil_moisture=max(all_soil) if all_soil else None,
        avg_light=statistics.mean(all_light) if all_light else None,
        per_sensor=per_sensor,
        water_warnings=sorted(set(water_warnings)),
    )
