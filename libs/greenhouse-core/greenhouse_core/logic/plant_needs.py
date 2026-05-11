"""Pure functions for plant care data interpretation."""

import statistics

from greenhouse_core.constants import DEFAULT_SOIL_MOISTURE_MAX, DEFAULT_SOIL_MOISTURE_MIN


def get_ideal_temp_range(plant_care_data: list[dict]) -> tuple[float, float] | None:
    """Get ideal temperature range for cluster from plant database."""
    mins = [d.get("ideal_temp_min_c") for d in plant_care_data if d.get("ideal_temp_min_c")]
    maxs = [d.get("ideal_temp_max_c") for d in plant_care_data if d.get("ideal_temp_max_c")]
    if not mins or not maxs:
        return None
    return (min(mins), max(maxs))


def get_ideal_humidity_range(plant_care_data: list[dict]) -> tuple[float, float] | None:
    """Get ideal humidity range for cluster from plant database."""
    mins = [d.get("ideal_humidity_min") for d in plant_care_data if d.get("ideal_humidity_min")]
    maxs = [d.get("ideal_humidity_max") for d in plant_care_data if d.get("ideal_humidity_max")]
    if not mins or not maxs:
        return None
    return (min(mins), max(maxs))


def parse_moisture_target(target: str) -> tuple[float, float]:
    """Parse soil moisture target range string like '45-65' to tuple (45, 65)."""
    try:
        parts = target.split("-")
        return (float(parts[0]), float(parts[1]))
    except Exception:
        return (DEFAULT_SOIL_MOISTURE_MIN, DEFAULT_SOIL_MOISTURE_MAX)


def analyze_water_needs(plant_care_data: list[dict]) -> str:
    """Determine average water needs level for cluster from plant database."""
    needs_map = {"low": 1, "medium": 2, "high": 3}
    values = [needs_map.get(d.get("water_needs", "medium"), 2) for d in plant_care_data]
    if not values:
        return "medium"
    avg = statistics.mean(values)
    if avg < 1.5:
        return "low"
    elif avg > 2.5:
        return "high"
    return "medium"
