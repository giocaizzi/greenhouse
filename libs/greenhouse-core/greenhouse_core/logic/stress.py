"""Stress condition detection for irrigation decisions."""

from greenhouse_core.constants import SOIL_MOISTURE_CRITICAL, SOIL_MOISTURE_LOW, SOIL_MOISTURE_SATURATED
from greenhouse_core.logic.decision import SensorSnapshot, StressIndicators, Trends
from greenhouse_core.logic.plant_needs import get_ideal_humidity_range, get_ideal_temp_range
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.utils import effective_light_threshold


def detect_stress_conditions(
    db: IrrigationRepository,
    plant_db: PlantDatabase,
    cluster_id: int,
    snapshot: SensorSnapshot,
    trends: Trends,
) -> StressIndicators:
    """Detect stress conditions from a sensor snapshot and trend signals."""
    stress = StressIndicators()
    plants = db.get_plants_in_cluster(cluster_id)
    plant_care = [plant_db.get_care_data(species=p.species, category=p.category) for p in plants]

    if snapshot.water_warnings:
        stress.water_warning = f"device alert on: {', '.join(snapshot.water_warnings)}"

    if snapshot.avg_env_humidity is not None and plant_care:
        hum_range = get_ideal_humidity_range(plant_care)
        if hum_range and snapshot.avg_env_humidity < hum_range[0] - 20:
            stress.low_env_humidity = (
                f"very dry air ({snapshot.avg_env_humidity:.0f}% vs ideal ≥{hum_range[0]:.0f}%) — high transpiration"
            )

    if snapshot.avg_light is not None and plant_care:
        min_lux_needed = max((d.get("ideal_light_lux_min", 0) for d in plant_care), default=0)
        seasonal_min = effective_light_threshold(min_lux_needed)
        if min_lux_needed > 0 and snapshot.avg_light < seasonal_min * 0.4:
            stress.low_light = (
                f"insufficient light ({snapshot.avg_light:.0f} lux vs seasonal min {seasonal_min:.0f}) — "
                f"reduced transpiration and growth"
            )

    if snapshot.avg_soil_moisture is not None:
        avg_soil = snapshot.avg_soil_moisture
        if avg_soil < SOIL_MOISTURE_CRITICAL:
            stress.water_stress = f"critical low ({avg_soil:.0f}%)"
            if trends.soil_moisture_trend == "declining":
                stress.water_stress += " + declining"
        elif avg_soil < SOIL_MOISTURE_LOW and trends.soil_moisture_trend == "declining":
            if trends.soil_moisture_delta < -10:
                stress.water_stress = f"low ({avg_soil:.0f}%) + steep decline ({trends.soil_moisture_delta:.0f}%)"

    if snapshot.avg_temperature is not None and plant_care:
        temp_range = get_ideal_temp_range(plant_care)
        if temp_range and snapshot.avg_temperature > temp_range[1] + 5:
            if trends.temperature_trend == "rising":
                stress.heat_stress = f"high temp ({snapshot.avg_temperature:.0f}°C) + rising"
            else:
                stress.heat_stress = f"above ideal ({snapshot.avg_temperature:.0f}°C > {temp_range[1]:.0f}°C)"

    if snapshot.avg_soil_moisture is not None and snapshot.avg_soil_moisture > SOIL_MOISTURE_SATURATED:
        if trends.irrigation_frequency_high:
            stress.over_watering = f"soil saturated ({snapshot.avg_soil_moisture:.0f}%) + high frequency"
        elif trends.soil_moisture_trend == "rising":
            stress.over_watering = f"soil very wet ({snapshot.avg_soil_moisture:.0f}%) + rising"

    return stress
