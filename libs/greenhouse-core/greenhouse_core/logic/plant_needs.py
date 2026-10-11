"""Pure functions for plant care data interpretation."""

import math
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from greenhouse_core.constants import (
    DEFAULT_SOIL_MOISTURE_MAX,
    DEFAULT_SOIL_MOISTURE_MIN,
    DEFAULT_SOIL_MOISTURE_TARGET,
    SENSOR_PHYSICAL_RANGES,
    WATER_NEEDS_HIGH_MEAN,
    WATER_NEEDS_LOW_MEAN,
)
from greenhouse_core.logic.decision import SensorSnapshot


def _widest_range(plant_care_data: list[dict[str, Any]], min_key: str, max_key: str) -> tuple[float, float] | None:
    """(lowest minimum, highest maximum) across the plants, or None when either side has no value.

    ``is not None``, not truthiness: 0 °C or 0 % is a real bound (a frost-hardy
    plant's minimum), not a missing one.
    """
    mins: list[Any] = [d[min_key] for d in plant_care_data if d.get(min_key) is not None]
    maxs: list[Any] = [d[max_key] for d in plant_care_data if d.get(max_key) is not None]
    if not mins or not maxs:
        return None
    return (min(mins), max(maxs))


def get_ideal_temp_range(plant_care_data: list[dict[str, Any]]) -> tuple[float, float] | None:
    """Get ideal temperature range for cluster from plant database."""
    return _widest_range(plant_care_data, "ideal_temp_min_c", "ideal_temp_max_c")


def get_ideal_humidity_range(plant_care_data: list[dict[str, Any]]) -> tuple[float, float] | None:
    """Get ideal humidity range for cluster from plant database."""
    return _widest_range(plant_care_data, "ideal_humidity_min", "ideal_humidity_max")


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


class _HasId(Protocol):
    id: int


@dataclass(frozen=True, slots=True)
class SoilNow:
    """One sensor's current soil reading and the moisture band it is judged against.

    ``band`` is the linked plant's own target; an unlinked sensor (or one whose
    plant is unknown) is judged against the cluster's widest band.
    """

    name: str
    value: float
    band: tuple[float, float]


def cluster_band(plant_care: Iterable[Mapping[str, Any]]) -> tuple[float, float]:
    """The widest moisture band across the cluster's plants: (lowest minimum, highest maximum)."""
    ranges = [moisture_target_range(c) for c in plant_care]
    if not ranges:
        return (DEFAULT_SOIL_MOISTURE_MIN, DEFAULT_SOIL_MOISTURE_MAX)
    return min(r[0] for r in ranges), max(r[1] for r in ranges)


def plant_bands(plants: Iterable[_HasId], plant_care: Iterable[Mapping[str, Any]]) -> dict[int, tuple[float, float]]:
    """Each plant's own moisture band, keyed by plant id (``plant_care`` is aligned with ``plants``)."""
    return {plant.id: moisture_target_range(care) for plant, care in zip(plants, plant_care, strict=True)}


def current_soil(
    snapshot: SensorSnapshot, bands: Mapping[int, tuple[float, float]], fallback: tuple[float, float]
) -> list[SoilNow]:
    """Every sensor with a current soil reading, paired with the band its plant needs.

    The per-sensor band is what keeps one plant's needs from deciding for
    another: a cactus in the cluster must not lower the dry threshold of the
    Monstera next to it.
    """
    return [
        SoilNow(s.name, s.latest_soil_moisture, bands.get(s.plant_id, fallback) if s.plant_id is not None else fallback)
        for s in snapshot.per_sensor
        if s.latest_soil_moisture is not None
    ]
