"""Sensor data aggregation for irrigation decisions."""

import statistics

from tuya_irrigation_core.repository import IrrigationRepository


def get_recent_sensor_data(db: IrrigationRepository, cluster_id: int, hours: int = 24) -> dict:
    """Get sensor data from recent readings, per-sensor and aggregated.

    Returns dict with:
    - avg_temperature, avg_env_humidity, avg_soil_moisture, avg_light (cluster-wide)
    - min_soil_moisture, max_soil_moisture (for conflict detection)
    - per_sensor: list of {sensor_id, plant_id, name, avg_soil, avg_temp, ...}
    """
    sensors = db.get_sensors_in_cluster(cluster_id)
    if not sensors:
        return {}

    all_temps = []
    all_env_humidity = []
    all_soil = []
    all_light = []
    water_warnings = []
    per_sensor = []

    for sensor in sensors:
        readings = db.get_recent_readings(sensor.id, hours=hours)
        s_temps = []
        s_humidity = []
        s_soil = []

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
            if r.light is not None and r.light > 15:  # exclude night readings
                all_light.append(r.light)
            if r.water_warning:
                water_warnings.append(sensor.name)

        sensor_info = {
            "sensor_id": sensor.id,
            "plant_id": sensor.plant_id,
            "name": sensor.name,
        }
        if s_temps:
            sensor_info["avg_temperature"] = statistics.mean(s_temps)
        if s_humidity:
            sensor_info["avg_humidity"] = statistics.mean(s_humidity)
        if s_soil:
            sensor_info["avg_soil_moisture"] = statistics.mean(s_soil)
        per_sensor.append(sensor_info)

    data = {"per_sensor": per_sensor}
    if all_temps:
        data["avg_temperature"] = statistics.mean(all_temps)
    if all_env_humidity:
        data["avg_env_humidity"] = statistics.mean(all_env_humidity)
    if water_warnings:
        data["water_warnings"] = list(set(water_warnings))
    if all_soil:
        data["avg_soil_moisture"] = statistics.mean(all_soil)
        data["min_soil_moisture"] = min(all_soil)
        data["max_soil_moisture"] = max(all_soil)
    if all_light:
        data["avg_light"] = statistics.mean(all_light)

    return data
