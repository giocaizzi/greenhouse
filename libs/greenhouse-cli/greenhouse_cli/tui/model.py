"""Derive display summaries from raw API payloads (pure — no I/O, no widgets)."""

from __future__ import annotations

from dataclasses import dataclass, field

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
    last_event: dict | None = None
    decision: dict | None = None
    sparkline: list[float] = field(default_factory=list)

    @property
    def mood(self) -> Mood:
        return mood_for(self.min_moisture, self.band_min, self.band_max)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def is_watering(last_event: dict | None, reference: int | None = None) -> bool:
    """True while the irrigator's last ``start`` event is still within its duration."""
    if not last_event or last_event.get("action") != "start":
        return False
    minutes = last_event.get("duration_minutes") or 0
    return (reference if reference is not None else now()) < last_event["timestamp"] + minutes * 60


def sparkline_points(chart: dict | None) -> list[float]:
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


def summarize(status: dict, chart: dict | None = None) -> ClusterSummary:
    """Build a :class:`ClusterSummary` from ``GET /clusters/{id}/status`` (+ optional chart-data).

    Args:
        status: The cluster status payload.
        chart: Optional ``soil_moisture`` chart payload; supplies the threshold
            band and the sparkline.
    """
    cluster = status["cluster"]
    threshold = (chart or {}).get("threshold") or {}
    summary = ClusterSummary(
        id=cluster["id"],
        name=cluster["name"],
        environment=cluster.get("environment", "indoor"),
        location=cluster.get("location"),
        band_min=threshold.get("min"),
        band_max=threshold.get("max"),
        decision=status.get("decision"),
        sparkline=sparkline_points(chart),
    )

    moisture_by_plant: dict[int, float] = {}
    moistures, temps, hums, lights, stamps = [], [], [], [], []
    for sensor in status.get("sensors", []):
        reading = sensor.get("last_reading")
        if not reading:
            continue
        stamps.append(reading["timestamp"])
        if reading.get("soil_moisture") is not None:
            moistures.append(reading["soil_moisture"])
            if sensor.get("plant_id") is not None:
                moisture_by_plant[sensor["plant_id"]] = reading["soil_moisture"]
        if reading.get("temperature") is not None:
            temps.append(reading["temperature"])
        if reading.get("env_humidity") is not None:
            hums.append(reading["env_humidity"])
        if reading.get("light") is not None:
            lights.append(reading["light"])

    summary.min_moisture = min(moistures) if moistures else None
    summary.temperature = _mean(temps)
    summary.humidity = _mean(hums)
    summary.light = _mean(lights)
    summary.newest_reading_at = max(stamps) if stamps else None

    for plant in status.get("plants", []):
        moisture = moisture_by_plant.get(plant["id"])
        summary.plants.append(
            PlantView(
                id=plant["id"],
                species=plant["species"],
                category=plant.get("category"),
                moisture=moisture,
                mood=mood_for(moisture, summary.band_min, summary.band_max),
            )
        )
    with_data = [p for p in summary.plants if p.moisture is not None]
    if with_data:
        summary.driest = min(with_data, key=lambda p: p.moisture)
    elif summary.plants:
        summary.driest = summary.plants[0]

    irrigator = status.get("irrigator")
    if irrigator:
        summary.irrigator_id = irrigator["id"]
        summary.irrigator_name = irrigator["name"]
        summary.last_event = irrigator.get("last_event")
        summary.watering = is_watering(summary.last_event)
    return summary
