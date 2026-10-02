"""Per-plant health score computation and daily snapshot service."""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self, TypedDict

from greenhouse_core.constants import HEALTH_SCORE_WINDOW_DAYS
from greenhouse_core.learning.profiling import get_plant_profile
from greenhouse_core.logic.cleaning import clean_readings
from greenhouse_core.logic.plant_needs import moisture_target_range
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository

if TYPE_CHECKING:
    from greenhouse_core.logic.cleaning import CleanedReading
    from greenhouse_core.models import Sensor


class HealthScore(TypedDict):
    """Composite health score of one plant; key order is the serialized order."""

    score: float | None
    soil_in_band_pct: float | None
    temp_in_band_pct: float | None
    humidity_in_band_pct: float | None
    efficiency: float | None
    sample_count: int


def _empty_score() -> HealthScore:
    """Score of an unknown plant — a fresh dict each call, so callers may mutate it."""
    return {
        "score": None,
        "soil_in_band_pct": None,
        "temp_in_band_pct": None,
        "humidity_in_band_pct": None,
        "efficiency": None,
        "sample_count": 0,
    }


@dataclass(frozen=True, slots=True)
class _HealthBands:
    """The care bands a plant's readings are judged against."""

    soil: tuple[float, float]
    temp: tuple[float | None, float | None]
    humidity: tuple[float | None, float | None]

    @classmethod
    def from_care(cls, care: Mapping[str, Any]) -> Self:
        """Read the bands from plant care data; temp/humidity bounds may be missing."""
        return cls(
            soil=moisture_target_range(care),
            temp=(care.get("ideal_temp_min_c"), care.get("ideal_temp_max_c")),
            humidity=(care.get("ideal_humidity_min"), care.get("ideal_humidity_max")),
        )


def _in_band_pct(values: Sequence[float], lo: float, hi: float) -> float | None:
    """Percentage of ``values`` inside ``[lo, hi]``; None when there is nothing to judge."""
    if not values:
        return None
    in_band = sum(1 for v in values if lo <= v <= hi)
    return in_band / len(values) * 100


def _band_percentages(
    readings: "Sequence[CleanedReading]", bands: _HealthBands
) -> tuple[float | None, float | None, float | None]:
    """Time-in-band per metric; temp/humidity only count when the plant has both bounds."""
    if not readings:
        return None, None, None
    soil_min, soil_max = bands.soil
    soil = _in_band_pct([r.soil_moisture for r in readings if r.soil_moisture is not None], soil_min, soil_max)
    temp: float | None = None
    temp_min, temp_max = bands.temp
    if temp_min is not None and temp_max is not None:
        temp = _in_band_pct([r.temperature for r in readings if r.temperature is not None], temp_min, temp_max)
    humidity: float | None = None
    hum_min, hum_max = bands.humidity
    if hum_min is not None and hum_max is not None:
        humidity = _in_band_pct([r.env_humidity for r in readings if r.env_humidity is not None], hum_min, hum_max)
    return soil, temp, humidity


def _composite_score(components: Sequence[float]) -> float | None:
    """Mean of the available components, rounded and clipped to [0, 100]."""
    if not components:
        return None
    return float(max(0.0, min(100.0, round(sum(components) / len(components)))))


class PlantHealthService:
    """Computes and persists a 0–100 composite health score for each plant."""

    def __init__(self, repo: IrrigationRepository, plant_db: PlantDatabase):
        self._repo = repo
        self._plant_db = plant_db

    def compute_score(self, plant_id: int, *, days: int = HEALTH_SCORE_WINDOW_DAYS) -> HealthScore:
        """Compute the composite health score for a single plant over the last ``days``.

        Score formula (0–100): mean of whichever subset of the four components
        is available — ``soil_in_band_pct``, ``temp_in_band_pct``,
        ``humidity_in_band_pct``, and ``efficiency * 100`` — clipped to [0, 100].
        Each component is included only when the underlying data exists (e.g.
        efficiency is skipped when there are fewer than 3 irrigation events).

        Args:
            plant_id: Database ID of the plant to evaluate.
            days: Look-back window in days for sensor readings.

        Returns:
            dict with keys: score (float | None), soil_in_band_pct (float | None),
            temp_in_band_pct (float | None), humidity_in_band_pct (float | None),
            efficiency (float | None), sample_count (int).
            score is None when no readings and no efficiency are available.
        """
        plant = self._repo.get_plant(plant_id)
        if not plant:
            return _empty_score()

        care = self._plant_db.get_care_data(species=plant.species, category=plant.category)
        bands = _HealthBands.from_care(care)

        sensors = list(plant.sensors)
        all_readings = self._pooled_clean_readings(sensors, days)
        soil_in_band_pct, temp_in_band_pct, humidity_in_band_pct = _band_percentages(all_readings, bands)
        efficiency = self._first_profile_efficiency(sensors, days)

        components = [
            c
            for c in [
                soil_in_band_pct,
                temp_in_band_pct,
                humidity_in_band_pct,
                efficiency * 100 if efficiency is not None else None,
            ]
            if c is not None
        ]

        return {
            "score": _composite_score(components),
            "soil_in_band_pct": soil_in_band_pct,
            "temp_in_band_pct": temp_in_band_pct,
            "humidity_in_band_pct": humidity_in_band_pct,
            "efficiency": efficiency,
            "sample_count": len(all_readings),
        }

    def _pooled_clean_readings(self, sensors: "Sequence[Sensor]", days: int) -> "list[CleanedReading]":
        """Every sensor's cleaned readings over the window, pooled in sensor order."""
        all_readings: list[CleanedReading] = []
        for sensor in sensors:
            # Cleaned view, per sensor, before pooling: the score is a judgement
            # about how much time the plant spent in band, and a probe glitch
            # must not cost (or buy) the plant health points.
            all_readings.extend(clean_readings(self._repo.get_recent_readings(sensor.id, hours=days * 24)))
        return all_readings

    def _first_profile_efficiency(self, sensors: "Sequence[Sensor]", days: int) -> float | None:
        """Efficiency of the first sensor that has a learned profile (later sensors are not read)."""
        for sensor in sensors:
            profile = get_plant_profile(self._repo, sensor, days=days)
            if profile is not None:
                return profile.efficiency_score
        return None

    def snapshot_daily(self) -> int:
        """Compute today's health score for every plant and upsert to plant_health_daily.

        Returns:
            Number of rows written (one per plant regardless of whether it was
            inserted or updated).
        """
        date_key = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        now = int(time.time())
        plants = self._repo.list_all_plants()
        rows_written = 0
        for plant in plants:
            result = self.compute_score(plant.id)
            score = result["score"]
            if score is None:
                continue
            self._repo.upsert_plant_health(
                plant_id=plant.id,
                date_key=date_key,
                score=score,
                soil_in_band_pct=result["soil_in_band_pct"],
                temp_in_band_pct=result["temp_in_band_pct"],
                humidity_in_band_pct=result["humidity_in_band_pct"],
                efficiency=result["efficiency"],
                sample_count=result["sample_count"],
                timestamp=now,
            )
            rows_written += 1
        return rows_written
