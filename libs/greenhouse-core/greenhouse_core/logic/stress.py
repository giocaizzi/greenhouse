"""Stress condition detection for irrigation decisions."""

from __future__ import annotations

from typing import Any

from greenhouse_core.constants import (
    CRITICAL_STRESS_BELOW_TARGET_MARGIN,
    DEFAULT_SOIL_MOISTURE_MAX,
    DEFAULT_SOIL_MOISTURE_MIN,
    SOIL_MOISTURE_CRITICAL,
    SOIL_MOISTURE_LOW,
    SOIL_MOISTURE_SATURATED,
    STRESS_HEAT_OFFSET_C,
    STRESS_HUMIDITY_DEFICIT,
    STRESS_LOW_LIGHT_FRACTION,
    STRESS_STEEP_DECLINE_DELTA,
)
from greenhouse_core.logic.decision import SensorSnapshot, StressIndicators, Trends
from greenhouse_core.logic.plant_needs import get_ideal_humidity_range, get_ideal_temp_range, moisture_target_range
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


def _low_light(snapshot: SensorSnapshot, care: list[dict[str, Any]], month: int | None) -> str | None:
    """Daylight well below the most demanding plant's seasonally adjusted minimum (``month`` picks the season)."""
    if snapshot.avg_light is None or not care:
        return None
    min_lux_needed = max((d.get("ideal_light_lux_min", 0) for d in care), default=0)
    seasonal_min = effective_light_threshold(min_lux_needed, month)
    if min_lux_needed > 0 and snapshot.avg_light < seasonal_min * STRESS_LOW_LIGHT_FRACTION:
        return (
            f"insufficient light ({snapshot.avg_light:.0f} lux vs seasonal min {seasonal_min:.0f}) — "
            f"reduced transpiration and growth"
        )
    return None


def _critical_thresholds(care: list[dict[str, Any]]) -> tuple[float, float]:
    """(critical, low) soil thresholds for these plants, never above the global 30 % / 40 %.

    Keyed on the lowest minimum target across the cluster's plants (the same
    parser as the soil rule): a band that tolerates dry soil lowers both, a
    thirsty one keeps the global values.
    """
    lowest_min = min((moisture_target_range(c)[0] for c in care), default=DEFAULT_SOIL_MOISTURE_MIN)
    critical = min(float(SOIL_MOISTURE_CRITICAL), lowest_min - CRITICAL_STRESS_BELOW_TARGET_MARGIN)
    return critical, critical + (SOIL_MOISTURE_LOW - SOIL_MOISTURE_CRITICAL)


def _water_stress(snapshot: SensorSnapshot, trends: Trends, care: list[dict[str, Any]]) -> str | None:
    """Critically dry soil, or low soil still falling steeply — judged on the driest plant *now*.

    Invariant #2 (the driest plant drives the call) holds here too: averaging a
    wilting plant at 10 % with a watered one at 60 % would hide the emergency.
    But this override is terminal and waters at stress dosage, so it keys on each
    sensor's newest cleaned reading, not on the 24 h minimum — a plant that was
    dry yesterday and has been watered since is not in critical stress. The
    thresholds follow the plants' own targets (:func:`_critical_thresholds`).
    """
    driest = min(snapshot.latest_soil_moisture, default=None)
    if driest is None:
        return None
    critical, low = _critical_thresholds(care)
    if driest < critical:
        declining = " + declining" if trends.soil_moisture_trend == "declining" else ""
        return f"critical low ({driest:.0f}%){declining}"
    if (
        driest < low
        and trends.soil_moisture_trend == "declining"
        and trends.soil_moisture_delta < STRESS_STEEP_DECLINE_DELTA
    ):
        return f"low ({driest:.0f}%) + steep decline ({trends.soil_moisture_delta:.0f}%)"
    return None


def _wet_plant_present(snapshot: SensorSnapshot, care: list[dict[str, Any]]) -> bool:
    """Some sensor's current reading sits above the cluster's widest moisture band.

    A stress run would drown that plant; the mixed case belongs to the soil
    rule's short conflict burst (and a stuck-low probe beside a wet plant is
    more likely a fault than an emergency).
    """
    if not snapshot.latest_soil_moisture:
        return False
    band_top = max((moisture_target_range(c)[1] for c in care), default=DEFAULT_SOIL_MOISTURE_MAX)
    return max(snapshot.latest_soil_moisture) > band_top


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


def detect_stress_conditions(  # noqa: PLR0913 — five pinned positionals (tests call them) + the keyword-only month
    db: IrrigationRepository,
    plant_db: PlantDatabase,
    cluster_id: int,
    snapshot: SensorSnapshot,
    trends: Trends,
    *,
    month: int | None = None,
) -> StressIndicators:
    """Detect stress conditions from a sensor snapshot and trend signals.

    ``month`` (1-12) selects the seasonal light factor for the low-light check;
    the engine passes the month in the preferences timezone so light and the
    seasonal multiplier agree on the season. ``None`` keeps the current UTC month.
    """
    stress = StressIndicators()
    plants = db.get_plants_in_cluster(cluster_id)
    plant_care = [plant_db.get_care_data(species=p.species, category=p.category) for p in plants]

    # Assign only detected conditions, in field order, so `model_fields_set` is unchanged.
    if (water_warning := _water_warning(snapshot)) is not None:
        stress.water_warning = water_warning
    if (low_env_humidity := _low_env_humidity(snapshot, plant_care)) is not None:
        stress.low_env_humidity = low_env_humidity
    if (low_light := _low_light(snapshot, plant_care, month)) is not None:
        stress.low_light = low_light
    over_watering = _over_watering(snapshot, trends)
    # Over-watering, or any plant currently above its band, vetoes the water-stress override.
    water_stress = _water_stress(snapshot, trends, plant_care)
    if water_stress is not None and over_watering is None and not _wet_plant_present(snapshot, plant_care):
        stress.water_stress = water_stress
    if (heat_stress := _heat_stress(snapshot, trends, plant_care)) is not None:
        stress.heat_stress = heat_stress
    if over_watering is not None:
        stress.over_watering = over_watering
    return stress
