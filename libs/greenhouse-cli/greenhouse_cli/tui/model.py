"""Derive display summaries from raw API payloads (pure — no I/O, no widgets)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from greenhouse_cli.tui.formatting import now
from greenhouse_cli.tui.sprites import Mood, mood_for


@dataclass
class PlantView:
    """One plant joined with the latest reading of its own sensor."""

    id: int
    species: str
    category: str | None
    moisture: float | None
    mood: Mood


@dataclass
class ClusterSummary:
    """Everything a dashboard card needs about one cluster."""

    id: int
    name: str
    environment: str
    location: str | None
    min_moisture: float | None = None
    temperature: float | None = None
    humidity: float | None = None
    light: float | None = None
    newest_reading_at: int | None = None
    band_min: float | None = None
    band_max: float | None = None
    driest: PlantView | None = None
    plants: list[PlantView] = field(default_factory=list)
    irrigator_id: int | None = None
    irrigator_name: str | None = None
    watering: bool = False
    last_event: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    sparkline: list[float] = field(default_factory=list)

    @property
    def mood(self) -> Mood:
        """The sprite mood for the cluster's driest reading against its target band."""
        return mood_for(self.min_moisture, self.band_min, self.band_max)


def _mean(values: list[float]) -> float | None:
    """Arithmetic mean, ``None`` without values (temperature / humidity / light; moisture uses the minimum)."""
    return sum(values) / len(values) if values else None


def is_watering(last_event: dict[str, Any] | None, reference: int | None = None) -> bool:
    """True while the irrigator's last ``start`` event is still within its duration."""
    if not last_event or last_event.get("action") != "start":
        return False
    minutes = last_event.get("duration_minutes") or 0
    watering: bool = (reference if reference is not None else now()) < last_event["timestamp"] + minutes * 60
    return watering


def sparkline_points(chart: dict[str, Any] | None) -> list[float]:
    """Collapse a multi-sensor chart payload into one series (per-timestamp minimum).

    The minimum mirrors the engine's "driest plant drives the call" rule.
    """
    if not chart:
        return []
    merged: dict[int, float] = {}
    for dataset in chart.get("datasets", []):
        for ts, value in dataset.get("points", []):
            merged[ts] = min(value, merged.get(ts, value))
    return [merged[ts] for ts in sorted(merged)]


def _band(chart: dict[str, Any] | None) -> tuple[float | None, float | None]:
    """The chart's ideal soil-moisture band ``(min, max)``; ``(None, None)`` without a threshold."""
    threshold = (chart or {}).get("threshold") or {}
    return threshold.get("min"), threshold.get("max")


def _present(readings: list[dict[str, Any]], key: str) -> list[float]:
    """The ``key`` values of the readings that carry one, in sensor order."""
    return [reading[key] for reading in readings if reading.get(key) is not None]


def _moisture_by_plant(sensors: list[dict[str, Any]]) -> dict[int, float]:
    """Latest soil moisture per plant, from the sensors bound to a plant (a later sensor wins)."""
    moisture: dict[int, float] = {}
    for sensor in sensors:
        reading = sensor.get("last_reading")
        if reading and reading.get("soil_moisture") is not None and sensor.get("plant_id") is not None:
            moisture[sensor["plant_id"]] = reading["soil_moisture"]
    return moisture


def _plant_views(
    plants: list[dict[str, Any]], moisture_by_plant: dict[int, float], band: tuple[float | None, float | None]
) -> list[PlantView]:
    """One :class:`PlantView` per plant, its mood judged against the band."""
    views: list[PlantView] = []
    for plant in plants:
        moisture = moisture_by_plant.get(plant["id"])
        views.append(
            PlantView(
                id=plant["id"],
                species=plant["species"],
                category=plant.get("category"),
                moisture=moisture,
                mood=mood_for(moisture, band[0], band[1]),
            )
        )
    return views


def _driest(plants: list[PlantView]) -> PlantView | None:
    """The plant with the lowest moisture ("driest plant drives the call"); the first plant when none has data."""
    with_data = [p for p in plants if p.moisture is not None]
    if with_data:
        return min(with_data, key=lambda p: cast(float, p.moisture))
    return plants[0] if plants else None


def summarize(status: dict[str, Any], chart: dict[str, Any] | None = None) -> ClusterSummary:
    """Build a :class:`ClusterSummary` from ``GET /clusters/{id}/status`` (+ optional chart-data).

    Args:
        status: The cluster status payload.
        chart: Optional ``soil_moisture`` chart payload; supplies the threshold
            band and the sparkline.
    """
    cluster = status["cluster"]
    band_min, band_max = _band(chart)
    summary = ClusterSummary(
        id=cluster["id"],
        name=cluster["name"],
        environment=cluster.get("environment", "indoor"),
        location=cluster.get("location"),
        band_min=band_min,
        band_max=band_max,
        decision=status.get("decision"),
        sparkline=sparkline_points(chart),
    )

    sensors = status.get("sensors", [])
    readings = [sensor["last_reading"] for sensor in sensors if sensor.get("last_reading")]
    stamps = [reading["timestamp"] for reading in readings]
    moistures = _present(readings, "soil_moisture")
    summary.min_moisture = min(moistures) if moistures else None
    summary.temperature = _mean(_present(readings, "temperature"))
    summary.humidity = _mean(_present(readings, "env_humidity"))
    summary.light = _mean(_present(readings, "light"))
    summary.newest_reading_at = max(stamps) if stamps else None

    summary.plants = _plant_views(status.get("plants", []), _moisture_by_plant(sensors), (band_min, band_max))
    summary.driest = _driest(summary.plants)

    irrigator = status.get("irrigator")
    if irrigator:
        summary.irrigator_id = irrigator["id"]
        summary.irrigator_name = irrigator["name"]
        summary.last_event = irrigator.get("last_event")
        summary.watering = is_watering(summary.last_event)
    return summary
