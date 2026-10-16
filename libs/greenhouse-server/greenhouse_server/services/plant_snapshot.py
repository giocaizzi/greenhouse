"""Per-plant snapshot tiles and current conditions for the cluster page's live status panel.

The soil value is a judgement (is this plant inside its band?), so it comes from the
cleaned decision snapshot and the engine's own band resolution (``plant_needs.current_soil``).
Temperature, humidity, light and the reading age are only displayed, so they come from
each sensor's newest raw reading.
"""

import statistics
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, TypedDict

from greenhouse_core.constants import SENSOR_READING_STALE_SECONDS, SNAPSHOT_LOOKBACK_HOURS
from greenhouse_core.logic.decision import SensorSnapshot
from greenhouse_core.logic.plant_needs import current_soil, moisture_target_range
from greenhouse_core.logic.sensors import get_recent_sensor_data
from greenhouse_core.plant_db import PlantDatabase, plant_care_data
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.health import PlantHealthService

if TYPE_CHECKING:
    from greenhouse_core.models import Plant, SensorReading
    from greenhouse_server.services.cluster import SensorStatusRow


class PlantTile(TypedDict):
    """One plant's tile: live health score, current soil against its band, latest climate."""

    plant_id: int
    species: str
    score: float | None
    soil: float | None  # driest current cleaned soil reading of the plant's sensors
    band: tuple[float, float]
    temperature: float | None
    humidity: float | None
    reading_age_seconds: int | None  # newest raw reading of the plant's sensors
    sensor_count: int


class ClusterConditions(TypedDict):
    """Mean of the cluster sensors' current raw readings (older than the stale threshold are left out)."""

    temperature: float | None
    humidity: float | None
    light: float | None


class PlantSnapshots(TypedDict):
    """``plant_snapshots`` result: a tile per plant and the cluster's current conditions."""

    plants: list[PlantTile]
    conditions: ClusterConditions


def _mean(values: Sequence[float]) -> float | None:
    return statistics.mean(values) if values else None


def _newest(readings: "Sequence[SensorReading]") -> "SensorReading | None":
    return max(readings, key=lambda r: r.timestamp, default=None)


def _plant_soil(snapshot: SensorSnapshot, plant_id: int, band: tuple[float, float]) -> float | None:
    """The driest current soil value among the plant's sensors (invariant 2), judged with the engine's helper."""
    own = SensorSnapshot(per_sensor=[s for s in snapshot.per_sensor if s.plant_id == plant_id])
    values = [s.value for s in current_soil(own, {plant_id: band}, band)]
    return min(values, default=None)


def _conditions(rows: "Sequence[SensorStatusRow]", now: int) -> ClusterConditions:
    current = [
        r["last_reading"]
        for r in rows
        if r["last_reading"] is not None and now - r["last_reading"].timestamp <= SENSOR_READING_STALE_SECONDS
    ]
    return {
        "temperature": _mean([r.temperature for r in current if r.temperature is not None]),
        "humidity": _mean([r.env_humidity for r in current if r.env_humidity is not None]),
        "light": _mean([float(r.light) for r in current if r.light is not None]),
    }


def plant_snapshots(
    repo: IrrigationRepository,
    plant_db: PlantDatabase,
    cluster_id: int,
    plants: "Sequence[Plant]",
    sensor_rows: "Sequence[SensorStatusRow]",
) -> PlantSnapshots:
    """A tile per plant plus the cluster's current conditions.

    ``plants`` and ``sensor_rows`` come from ``ClusterService.get_cluster_status``, so the
    raw readings are not read twice; the cleaned snapshot is the decision's own.
    """
    now = int(time.time())
    snapshot = get_recent_sensor_data(repo, cluster_id, hours=SNAPSHOT_LOOKBACK_HOURS, now=now)
    health = PlantHealthService(repo, plant_db)
    tiles: list[PlantTile] = []
    for plant in plants:
        band = moisture_target_range(plant_care_data(plant_db, plant))
        own = [r["last_reading"] for r in sensor_rows if r["plant_id"] == plant.id and r["last_reading"] is not None]
        newest = _newest(own)
        tiles.append(
            {
                "plant_id": plant.id,
                "species": plant.species,
                "score": health.compute_score(plant.id)["score"],
                "soil": _plant_soil(snapshot, plant.id, band),
                "band": band,
                "temperature": newest.temperature if newest else None,
                "humidity": newest.env_humidity if newest else None,
                "reading_age_seconds": now - newest.timestamp if newest else None,
                "sensor_count": sum(1 for r in sensor_rows if r["plant_id"] == plant.id),
            }
        )
    return {"plants": tiles, "conditions": _conditions(sensor_rows, now)}
