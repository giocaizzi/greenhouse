"""Stress condition detection for irrigation decisions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from greenhouse_core.constants import (
    CRITICAL_STRESS_BELOW_TARGET_MARGIN,
    SOIL_MOISTURE_CRITICAL,
    SOIL_MOISTURE_LOW,
    SOIL_MOISTURE_SATURATED,
    STRESS_HEAT_OFFSET_C,
    STRESS_HUMIDITY_DEFICIT,
    STRESS_LOW_LIGHT_FRACTION,
    STRESS_STEEP_DECLINE_DELTA,
)
from greenhouse_core.logic.decision import SensorSnapshot, StressIndicators, Trends
from greenhouse_core.logic.plant_needs import (
    SoilNow,
    cluster_band,
    current_soil,
    get_ideal_humidity_range,
    get_ideal_temp_range,
    plant_bands,
)
from greenhouse_core.utils import effective_light_threshold

if TYPE_CHECKING:
    from greenhouse_core.plant_db import PlantDatabase
    from greenhouse_core.repository import IrrigationRepository


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


def _critical_thresholds(plant_min: float) -> tuple[float, float]:
    """(critical, low) soil thresholds for a plant with this minimum target, never above 30 % / 40 %.

    A band that tolerates dry soil lowers both; a thirsty one keeps the global
    values, so nothing triggers later than before for it.
    """
    critical = min(float(SOIL_MOISTURE_CRITICAL), plant_min - CRITICAL_STRESS_BELOW_TARGET_MARGIN)
    return critical, critical + (SOIL_MOISTURE_LOW - SOIL_MOISTURE_CRITICAL)


def _water_stress(soil: list[SoilNow], trends: Trends) -> str | None:
    """Critically dry soil, or low soil still falling steeply — per sensor, against its own plant.

    Invariant #2 (the driest plant drives the call) holds here too: averaging a
    wilting plant at 10 % with a watered one at 60 % would hide the emergency.
    The override is terminal and waters at stress dosage, so it keys on each
    sensor's current reading and on the band of the plant that sensor is linked
    to (:func:`_critical_thresholds`); one cactus in the cluster must not switch
    the emergency off for the Monstera beside it.
    """
    steep = trends.soil_moisture_trend == "declining" and trends.soil_moisture_delta < STRESS_STEEP_DECLINE_DELTA
    critical = [s.value for s in soil if s.value < _critical_thresholds(s.band[0])[0]]
    if critical:
        declining = " + declining" if trends.soil_moisture_trend == "declining" else ""
        return f"critical low ({min(critical):.0f}%){declining}"
    low = [s.value for s in soil if steep and s.value < _critical_thresholds(s.band[0])[1]]
    if low:
        return f"low ({min(low):.0f}%) + steep decline ({trends.soil_moisture_delta:.0f}%)"
    return None


def wettest_above_band(soil: list[SoilNow]) -> SoilNow | None:
    """The wettest sensor whose current reading is above its own plant's band, if any.

    A stress run (or acting on the device's soil-dry alarm) would drown that
    plant; the mixed case belongs to the soil rule's short conflict burst (and a
    stuck-low probe beside a wet plant is more likely a fault than an emergency).
    """
    above = [s for s in soil if s.value > s.band[1]]
    return max(above, key=lambda s: s.value) if above else None


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
    soil = current_soil(snapshot, plant_bands(plants, plant_care), cluster_band(plant_care))

    # Assign only detected conditions, in field order, so `model_fields_set` is unchanged.
    if (water_warning := _water_warning(snapshot)) is not None:
        stress.water_warning = water_warning
    if (low_env_humidity := _low_env_humidity(snapshot, plant_care)) is not None:
        stress.low_env_humidity = low_env_humidity
    if (low_light := _low_light(snapshot, plant_care, month)) is not None:
        stress.low_light = low_light
    over_watering = _over_watering(snapshot, trends)
    # Over-watering, or any plant currently above its band, vetoes the water-stress override.
    water_stress = _water_stress(soil, trends)
    if water_stress is not None and over_watering is None and wettest_above_band(soil) is None:
        stress.water_stress = water_stress
    if (heat_stress := _heat_stress(snapshot, trends, plant_care)) is not None:
        stress.heat_stress = heat_stress
    if over_watering is not None:
        stress.over_watering = over_watering
    return stress
