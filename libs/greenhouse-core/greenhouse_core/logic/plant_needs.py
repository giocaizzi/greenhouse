"""Pure functions for plant care data interpretation."""

import math
import statistics
from collections.abc import Mapping
from typing import Any

from greenhouse_core.constants import (
    DEFAULT_SOIL_MOISTURE_MAX,
    DEFAULT_SOIL_MOISTURE_MIN,
    DEFAULT_SOIL_MOISTURE_TARGET,
    SENSOR_PHYSICAL_RANGES,
    WATER_NEEDS_HIGH_MEAN,
    WATER_NEEDS_LOW_MEAN,
)


def get_ideal_temp_range(plant_care_data: list[dict[str, Any]]) -> tuple[float, float] | None:
    """Get ideal temperature range for cluster from plant database."""
    mins: list[Any] = [d.get("ideal_temp_min_c") for d in plant_care_data if d.get("ideal_temp_min_c")]
    maxs: list[Any] = [d.get("ideal_temp_max_c") for d in plant_care_data if d.get("ideal_temp_max_c")]
    if not mins or not maxs:
        return None
    return (min(mins), max(maxs))


def get_ideal_humidity_range(plant_care_data: list[dict[str, Any]]) -> tuple[float, float] | None:
    """Get ideal humidity range for cluster from plant database."""
    mins: list[Any] = [d.get("ideal_humidity_min") for d in plant_care_data if d.get("ideal_humidity_min")]
    maxs: list[Any] = [d.get("ideal_humidity_max") for d in plant_care_data if d.get("ideal_humidity_max")]
    if not mins or not maxs:
        return None
    return (min(mins), max(maxs))


def parse_moisture_target(target: str) -> tuple[float, float]:
    """Parse a soil-moisture target like ``"45-65"`` into a validated ``(low, high)`` band.

    This is the one soil-target parser (engine, monitor, health, forecast,
    learning, charts), so its validation applies to every caller alike:

    - an inverted range (``"65-45"``) is normalised to ``(45.0, 65.0)`` — the
      intent is unambiguous and a reversed band would make every reading
      "dry" and "wet" at once;
    - a bound that is not a finite number inside the soil sensor's physical
      range (``SENSOR_PHYSICAL_RANGES["soil_moisture"]``, 0-100 %) — negative,
      above 100, NaN or infinite — makes the whole target unusable, and the
      default band ``(DEFAULT_SOIL_MOISTURE_MIN, DEFAULT_SOIL_MOISTURE_MAX)``
      applies, the same fallback as for an unparsable string.

    Extra ``-``-separated parts are ignored (``"40-60-80"`` → ``(40.0, 60.0)``).
    """
    try:
        parts = target.split("-")
        low, high = float(parts[0]), float(parts[1])
    except Exception:  # noqa: BLE001
        return (DEFAULT_SOIL_MOISTURE_MIN, DEFAULT_SOIL_MOISTURE_MAX)
    floor, ceiling = SENSOR_PHYSICAL_RANGES["soil_moisture"]
    if not all(math.isfinite(v) and floor <= v <= ceiling for v in (low, high)):
        return (DEFAULT_SOIL_MOISTURE_MIN, DEFAULT_SOIL_MOISTURE_MAX)
    return (min(low, high), max(low, high))


def moisture_target_range(care: Mapping[str, Any]) -> tuple[float, float]:
    """Return a plant's soil-moisture band, falling back to ``DEFAULT_SOIL_MOISTURE_TARGET`` when unset."""
    return parse_moisture_target(care.get("soil_moisture_target", DEFAULT_SOIL_MOISTURE_TARGET))


def analyze_water_needs(plant_care_data: list[dict[str, Any]]) -> str:
    """Determine average water needs level for cluster from plant database."""
    needs_map = {"low": 1, "medium": 2, "high": 3}
    values = [needs_map.get(d.get("water_needs", "medium"), 2) for d in plant_care_data]
    if not values:
        return "medium"
    avg = statistics.mean(values)
    if avg < WATER_NEEDS_LOW_MEAN:
        return "low"
    if avg > WATER_NEEDS_HIGH_MEAN:
        return "high"
    return "medium"
