"""Stress condition detection for irrigation decisions."""

from __future__ import annotations

from typing import Any

from greenhouse_core.constants import (
    SOIL_MOISTURE_CRITICAL,
    SOIL_MOISTURE_LOW,
    SOIL_MOISTURE_SATURATED,
    STRESS_HEAT_OFFSET_C,
    STRESS_HUMIDITY_DEFICIT,
    STRESS_LOW_LIGHT_FRACTION,
    STRESS_STEEP_DECLINE_DELTA,
)
from greenhouse_core.logic.decision import SensorSnapshot, StressIndicators, Trends
from greenhouse_core.logic.plant_needs import get_ideal_humidity_range, get_ideal_temp_range
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.utils import effective_light_threshold


def _water_warning(snapshot: SensorSnapshot) -> str | None:
    """Device-reported water alarms, named by sensor."""
    if snapshot.water_warnings:
        return f"device alert on: {', '.join(snapshot.water_warnings)}"
    return None


def _low_env_humidity(snapshot: SensorSnapshot, care: list[dict[str, Any]]) -> str | None:
    """Air far drier than the plants' ideal minimum (high transpiration)."""
    if snapshot.avg_env_humidity is None or not care:
        return None
    hum_range = get_ideal_humidity_range(care)
    if hum_range and snapshot.avg_env_humidity < hum_range[0] - STRESS_HUMIDITY_DEFICIT:
        return f"very dry air ({snapshot.avg_env_humidity:.0f}% vs ideal ≥{hum_range[0]:.0f}%) — high transpiration"
    return None


def _low_light(snapshot: SensorSnapshot, care: list[dict[str, Any]]) -> str | None:
    """Daylight well below the most demanding plant's seasonally adjusted minimum."""
    if snapshot.avg_light is None or not care:
        return None
    min_lux_needed = max((d.get("ideal_light_lux_min", 0) for d in care), default=0)
    seasonal_min = effective_light_threshold(min_lux_needed)
    if min_lux_needed > 0 and snapshot.avg_light < seasonal_min * STRESS_LOW_LIGHT_FRACTION:
        return (
            f"insufficient light ({snapshot.avg_light:.0f} lux vs seasonal min {seasonal_min:.0f}) — "
            f"reduced transpiration and growth"
        )
    return None


def _water_stress(snapshot: SensorSnapshot, trends: Trends) -> str | None:
    """Critically dry soil, or low soil that is still falling steeply (keys on the average)."""
    avg_soil = snapshot.avg_soil_moisture
    if avg_soil is None:
        return None
    if avg_soil < SOIL_MOISTURE_CRITICAL:
        declining = " + declining" if trends.soil_moisture_trend == "declining" else ""
        return f"critical low ({avg_soil:.0f}%){declining}"
    if (
        avg_soil < SOIL_MOISTURE_LOW
        and trends.soil_moisture_trend == "declining"
        and trends.soil_moisture_delta < STRESS_STEEP_DECLINE_DELTA
    ):
        return f"low ({avg_soil:.0f}%) + steep decline ({trends.soil_moisture_delta:.0f}%)"
    return None


def _heat_stress(snapshot: SensorSnapshot, trends: Trends, care: list[dict[str, Any]]) -> str | None:
    """Temperature clearly above the plants' ideal maximum."""
    if snapshot.avg_temperature is None or not care:
        return None
    temp_range = get_ideal_temp_range(care)
    if temp_range and snapshot.avg_temperature > temp_range[1] + STRESS_HEAT_OFFSET_C:
        if trends.temperature_trend == "rising":
            return f"high temp ({snapshot.avg_temperature:.0f}°C) + rising"
        return f"above ideal ({snapshot.avg_temperature:.0f}°C > {temp_range[1]:.0f}°C)"
    return None


def _over_watering(snapshot: SensorSnapshot, trends: Trends) -> str | None:
    """Saturated soil that keeps being watered often or keeps getting wetter."""
    if snapshot.avg_soil_moisture is not None and snapshot.avg_soil_moisture > SOIL_MOISTURE_SATURATED:
        if trends.irrigation_frequency_high:
            return f"soil saturated ({snapshot.avg_soil_moisture:.0f}%) + high frequency"
        if trends.soil_moisture_trend == "rising":
            return f"soil very wet ({snapshot.avg_soil_moisture:.0f}%) + rising"
    return None


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

    # Assign only detected conditions, in field order, so `model_fields_set` is unchanged.
    if (water_warning := _water_warning(snapshot)) is not None:
        stress.water_warning = water_warning
    if (low_env_humidity := _low_env_humidity(snapshot, plant_care)) is not None:
        stress.low_env_humidity = low_env_humidity
    if (low_light := _low_light(snapshot, plant_care)) is not None:
        stress.low_light = low_light
    if (water_stress := _water_stress(snapshot, trends)) is not None:
        stress.water_stress = water_stress
    if (heat_stress := _heat_stress(snapshot, trends, plant_care)) is not None:
        stress.heat_stress = heat_stress
    if (over_watering := _over_watering(snapshot, trends)) is not None:
        stress.over_watering = over_watering
    return stress
