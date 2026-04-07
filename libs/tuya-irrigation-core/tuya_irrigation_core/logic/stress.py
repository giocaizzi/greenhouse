"""Stress condition detection for irrigation decisions."""

from tuya_irrigation_core.constants import SOIL_MOISTURE_CRITICAL, SOIL_MOISTURE_LOW, SOIL_MOISTURE_SATURATED
from tuya_irrigation_core.logic.plant_needs import get_ideal_humidity_range, get_ideal_temp_range
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_core.utils import effective_light_threshold


def detect_stress_conditions(
    db: IrrigationRepository,
    plant_db: PlantDatabase,
    cluster_id: int,
    sensor_data: dict,
    trends: dict,
) -> dict:
    """Detect stress conditions based on sensor data and trends.

    Returns dict with stress indicators:
    - water_warning:     sensor's own soil-dry alert (high confidence)
    - water_stress:      soil moisture consistently low + declining trend
    - heat_stress:       prolonged high temperature
    - over_watering:     soil too wet + high irrigation frequency
    - low_env_humidity:  very dry air → high transpiration
    - low_light:         insufficient lux for plant type → reduced growth/transpiration
    """
    stress = {}

    # Device water_warning: sensor's own dry-soil detection (high confidence signal)
    water_warnings = sensor_data.get("water_warnings", [])
    if water_warnings:
        stress["water_warning"] = f"device alert on: {', '.join(water_warnings)}"

    # Env humidity stress: sustained very dry air for plant type
    avg_env_hum = sensor_data.get("avg_env_humidity")
    plants_for_stress = db.get_plants_in_cluster(cluster_id)
    if avg_env_hum is not None and plants_for_stress:
        plant_care_data_s = [plant_db.get_care_data(species=p.species, category=p.category) for p in plants_for_stress]
        hum_range = get_ideal_humidity_range(plant_care_data_s)
        if hum_range and avg_env_hum < hum_range[0] - 20:
            stress["low_env_humidity"] = (
                f"very dry air ({avg_env_hum:.0f}% vs ideal ≥{hum_range[0]:.0f}%) — high transpiration"
            )

    # Light stress: sustained low light for plants that need it (seasonal threshold)
    avg_light_s = sensor_data.get("avg_light")
    if avg_light_s is not None and plants_for_stress:
        plant_care_data_s2 = [plant_db.get_care_data(species=p.species, category=p.category) for p in plants_for_stress]
        min_lux_needed = max((d.get("ideal_light_lux_min", 0) for d in plant_care_data_s2), default=0)
        seasonal_min = effective_light_threshold(min_lux_needed)
        if min_lux_needed > 0 and avg_light_s < seasonal_min * 0.4:
            stress["low_light"] = (
                f"insufficient light ({avg_light_s:.0f} lux vs seasonal min {seasonal_min:.0f}) — "
                f"reduced transpiration and growth"
            )

    # Water stress: soil moisture consistently low + declining trend
    avg_soil = sensor_data.get("avg_soil_moisture")
    if avg_soil:
        # Critical: below SOIL_MOISTURE_CRITICAL or below SOIL_MOISTURE_LOW with steep decline
        if avg_soil < SOIL_MOISTURE_CRITICAL:
            stress["water_stress"] = f"critical low ({avg_soil:.0f}%)"
            if trends.get("soil_moisture_trend") == "declining":
                stress["water_stress"] += " + declining"
        elif avg_soil < SOIL_MOISTURE_LOW and trends.get("soil_moisture_trend") == "declining":
            delta = trends.get("soil_moisture_delta", 0)
            if delta < -10:  # Steep decline
                stress["water_stress"] = f"low ({avg_soil:.0f}%) + steep decline ({delta:.0f}%)"

    # Heat stress: prolonged high temperature
    avg_temp = sensor_data.get("avg_temperature")
    plants = db.get_plants_in_cluster(cluster_id)
    if avg_temp and plants:
        plant_care_data = [plant_db.get_care_data(species=p.species, category=p.category) for p in plants]
        temp_range = get_ideal_temp_range(plant_care_data)
        if temp_range and avg_temp > temp_range[1] + 5:
            if trends.get("temperature_trend") == "rising":
                stress["heat_stress"] = f"high temp ({avg_temp:.0f}°C) + rising"
            else:
                stress["heat_stress"] = f"above ideal ({avg_temp:.0f}°C > {temp_range[1]:.0f}°C)"

    # Over-watering: soil consistently wet + high irrigation frequency
    if avg_soil and avg_soil > SOIL_MOISTURE_SATURATED:
        if trends.get("irrigation_frequency_high"):
            stress["over_watering"] = f"soil saturated ({avg_soil:.0f}%) + high frequency"
        elif trends.get("soil_moisture_trend") == "rising":
            stress["over_watering"] = f"soil very wet ({avg_soil:.0f}%) + rising"

    return stress
