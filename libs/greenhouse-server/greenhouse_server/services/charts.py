"""Chart payload builder shared by API and web routes."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select

from greenhouse_core.constants import DEFAULT_SOIL_MOISTURE_MAX, DEFAULT_SOIL_MOISTURE_MIN
from greenhouse_core.models import IrrigationEvent, Plant, Sensor
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.schemas import (
    HeatmapCell,
    HeatmapResponse,
    MultiMetricOverlayResponse,
    OverlayDataset,
    PlantHealthTimelineResponse,
)

Metric = Literal["soil_moisture", "temperature", "light", "env_humidity"]
ALLOWED_HOURS = {24, 168, 720}


def _parse_range(target: str | None) -> tuple[float | None, float | None]:
    if not target or "-" not in target:
        return (None, None)
    try:
        lo, hi = target.split("-", 1)
        return (float(lo), float(hi))
    except (ValueError, TypeError):
        return (None, None)


def _metric_field(metric: Metric) -> str:
    # Matches SensorReading column names.
    return metric


def build_plant_chart_payload(
    repo: IrrigationRepository,
    plant_db: PlantDatabase,
    plant_id: int,
    hours: int,
    metric: Metric,
) -> dict:
    plant: Plant | None = repo.session.get(Plant, plant_id)
    if plant is None:
        return {}

    # Use assignment-aware reading lookup so historical readings stay attributed
    # to the plant that actually owned the sensor at reading time. Filtering
    # by current `sensor.plant_id` re-attributes prior history whenever a
    # sensor is reassigned, producing misleading trends.
    datasets = _build_plant_sensor_datasets(repo, plant_id, hours, metric)
    events = _build_event_list(repo, plant.cluster_id, hours)
    threshold = _threshold_for_plant(plant, plant_db, metric)

    return {
        "metric": metric,
        "hours": hours,
        "datasets": datasets,
        "events": events,
        "threshold": threshold,
    }


def build_cluster_chart_payload(
    repo: IrrigationRepository,
    plant_db: PlantDatabase,
    cluster_id: int,
    hours: int,
    metric: Metric,
) -> dict:
    cluster = repo.get_cluster(cluster_id)
    if cluster is None:
        return {}

    sensors = repo.get_sensors_in_cluster(cluster_id)
    datasets = _build_sensor_datasets(repo, sensors, hours, metric)
    events = _build_event_list(repo, cluster_id, hours)
    threshold = _threshold_for_cluster(repo, plant_db, cluster_id, metric)

    return {
        "metric": metric,
        "hours": hours,
        "datasets": datasets,
        "events": events,
        "threshold": threshold,
    }


def _build_plant_sensor_datasets(
    repo: IrrigationRepository,
    plant_id: int,
    hours: int,
    metric: Metric,
) -> list[dict]:
    """Assignment-aware variant: readings are filtered to windows when the
    sensor was actually linked to this plant. One dataset per sensor that ever
    served this plant within the lookback window."""
    field = _metric_field(metric)
    since = int(time.time()) - hours * 3600
    readings = repo.readings_for_plant(plant_id, since_ts=since)
    by_sensor: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for r in readings:
        val = getattr(r, field, None)
        if val is None:
            continue
        by_sensor[r.sensor_id].append((r.timestamp, float(val)))

    sensor_names: dict[int, str] = {}
    if by_sensor:
        for s in repo.session.scalars(select(Sensor).where(Sensor.id.in_(by_sensor.keys()))):
            sensor_names[s.id] = s.name

    datasets = []
    for sensor_id, points in by_sensor.items():
        points.sort(key=lambda p: p[0])
        datasets.append(
            {
                "sensor_id": sensor_id,
                "sensor_name": sensor_names.get(sensor_id, f"sensor#{sensor_id}"),
                "points": points,
            }
        )
    return datasets


def _build_sensor_datasets(
    repo: IrrigationRepository,
    sensors: list[Sensor],
    hours: int,
    metric: Metric,
) -> list[dict]:
    datasets = []
    field = _metric_field(metric)
    for sensor in sensors:
        readings = repo.get_recent_readings(sensor.id, hours=hours)
        points: list[tuple[int, float]] = []
        for r in readings:
            val = getattr(r, field, None)
            if val is None:
                continue
            points.append((r.timestamp, float(val)))
        if not points:
            continue
        # readings come ordered DESC; chart wants ASC
        points.sort(key=lambda p: p[0])
        datasets.append({"sensor_id": sensor.id, "sensor_name": sensor.name, "points": points})
    return datasets


def _build_event_list(repo: IrrigationRepository, cluster_id: int, hours: int) -> list[dict]:
    irrigator = repo.get_irrigator_for_cluster(cluster_id)
    cutoff = int(time.time()) - (hours * 3600)
    events: list[dict] = []
    if irrigator is not None:
        for e in repo.get_recent_events(irrigator.id, hours=hours):
            if e.timestamp < cutoff:
                continue
            events.append(
                {
                    "timestamp": e.timestamp,
                    "action": e.action,
                    "duration_minutes": e.duration_minutes,
                }
            )
    events.sort(key=lambda e: e["timestamp"])
    return events


def _threshold_for_plant(plant: Plant, plant_db: PlantDatabase, metric: Metric) -> dict:
    if metric == "soil_moisture":
        if plant.water_needs:
            info = plant_db.get_water_needs_info(plant.water_needs)
            lo, hi = _parse_range(info.get("soil_moisture_target"))
            if lo is not None:
                return {"min": lo, "max": hi, "source": f"water_needs:{plant.water_needs}"}
        return {
            "min": float(DEFAULT_SOIL_MOISTURE_MIN),
            "max": float(DEFAULT_SOIL_MOISTURE_MAX),
            "source": "default",
        }
    if metric == "temperature":
        if plant.ideal_temp_min is not None or plant.ideal_temp_max is not None:
            return {"min": plant.ideal_temp_min, "max": plant.ideal_temp_max, "source": "ideal_temp"}
    if metric == "env_humidity":
        if plant.ideal_humidity_min is not None or plant.ideal_humidity_max is not None:
            return {
                "min": plant.ideal_humidity_min,
                "max": plant.ideal_humidity_max,
                "source": "ideal_humidity",
            }
    return {"min": None, "max": None, "source": "none"}


def _threshold_for_cluster(
    repo: IrrigationRepository, plant_db: PlantDatabase, cluster_id: int, metric: Metric
) -> dict:
    if metric == "soil_moisture":
        return {
            "min": float(DEFAULT_SOIL_MOISTURE_MIN),
            "max": float(DEFAULT_SOIL_MOISTURE_MAX),
            "source": "default",
        }
    # Cluster-scope temp/humidity: take min/max across plants if set.
    plants = repo.get_plants_in_cluster(cluster_id)
    if metric == "temperature":
        mins = [p.ideal_temp_min for p in plants if p.ideal_temp_min is not None]
        maxs = [p.ideal_temp_max for p in plants if p.ideal_temp_max is not None]
        if mins or maxs:
            return {
                "min": min(mins) if mins else None,
                "max": max(maxs) if maxs else None,
                "source": "plant_aggregate",
            }
    if metric == "env_humidity":
        mins = [p.ideal_humidity_min for p in plants if p.ideal_humidity_min is not None]
        maxs = [p.ideal_humidity_max for p in plants if p.ideal_humidity_max is not None]
        if mins or maxs:
            return {
                "min": min(mins) if mins else None,
                "max": max(maxs) if maxs else None,
                "source": "plant_aggregate",
            }
    return {"min": None, "max": None, "source": "none"}


# ---------------------------------------------------------------------------
# Premium viz builders
# ---------------------------------------------------------------------------

_LIGHT_MAX_LUX = 10_000.0  # scaling ceiling for light → 0-100 normalisation


def build_overlay_payload(
    repo: IrrigationRepository,
    cluster_id: int,
    hours: int,
) -> MultiMetricOverlayResponse | None:
    """Build the multi-metric overlay payload for a cluster.

    Collects soil moisture, env humidity, and light readings across all sensors
    in the cluster, normalises each series to 0-100, and merges irrigation events.
    Returns None if the cluster does not exist.
    """
    cluster = repo.get_cluster(cluster_id)
    if cluster is None:
        return None

    sensors = repo.get_sensors_in_cluster(cluster_id)
    cutoff = int(time.time()) - hours * 3600

    # Aggregate per-metric points: take mean across sensors per timestamp bucket (nearest minute).
    soil_buckets: dict[int, list[float]] = defaultdict(list)
    humidity_buckets: dict[int, list[float]] = defaultdict(list)
    light_buckets: dict[int, list[float]] = defaultdict(list)

    for sensor in sensors:
        for r in repo.get_recent_readings(sensor.id, hours=hours):
            ts = (r.timestamp // 60) * 60  # bucket to minute
            if r.soil_moisture is not None:
                soil_buckets[ts].append(float(r.soil_moisture))
            if r.env_humidity is not None:
                humidity_buckets[ts].append(float(r.env_humidity))
            if r.light is not None:
                light_buckets[ts].append(float(r.light))

    def _to_points(buckets: dict[int, list[float]], scale: float = 1.0) -> list[tuple[int, float]]:
        return sorted((ts, min(100.0, sum(v) / len(v) * scale)) for ts, v in buckets.items() if ts >= cutoff)

    datasets: list[OverlayDataset] = []
    if soil_buckets:
        datasets.append(OverlayDataset(metric="soil", points=_to_points(soil_buckets)))
    if humidity_buckets:
        datasets.append(OverlayDataset(metric="humidity", points=_to_points(humidity_buckets)))
    if light_buckets:
        scale = 100.0 / _LIGHT_MAX_LUX
        datasets.append(
            OverlayDataset(
                metric="light",
                points=_to_points(light_buckets, scale=scale),
                original_max=_LIGHT_MAX_LUX,
            )
        )

    raw_events = _build_event_list(repo, cluster_id, hours)

    return MultiMetricOverlayResponse(
        cluster_id=cluster_id,
        hours=hours,
        datasets=datasets,
        events=raw_events,  # type: ignore[arg-type]
        normalised=True,
    )


def build_heatmap_payload(
    repo: IrrigationRepository,
    cluster_id: int,
    days: int,
) -> HeatmapResponse | None:
    """Build the 7×24 irrigation heatmap payload for a cluster.

    Counts irrigation events per (weekday, hour) cell over the given look-back
    window. Returns None if the cluster does not exist.
    """
    cluster = repo.get_cluster(cluster_id)
    if cluster is None:
        return None

    cutoff = int(time.time()) - days * 86400
    irrigator = repo.get_irrigator_for_cluster(cluster_id)

    counts: dict[tuple[int, int], int] = defaultdict(int)
    minutes_map: dict[tuple[int, int], int] = defaultdict(int)

    if irrigator is not None:
        events = repo.session.scalars(
            select(IrrigationEvent).where(
                IrrigationEvent.irrigator_id == irrigator.id, IrrigationEvent.timestamp >= cutoff
            )
        )
        for ev in events:
            dt = datetime.fromtimestamp(ev.timestamp, tz=UTC)
            key = (dt.weekday(), dt.hour)
            counts[key] += 1
            minutes_map[key] += ev.duration_minutes or 0

    cells = [
        HeatmapCell(weekday=wd, hour=h, count=counts[(wd, h)], total_minutes=minutes_map[(wd, h)])
        for wd in range(7)
        for h in range(24)
        if counts[(wd, h)] > 0
    ]

    return HeatmapResponse(cluster_id=cluster_id, days=days, cells=cells)


def build_plant_health_timeline_payload(
    repo: IrrigationRepository,
    plant_id: int,
) -> PlantHealthTimelineResponse | None:
    """Build the 90-day daily health score timeline for a single plant.

    Health score per day is derived from the mean soil moisture reading clamped
    to [0, 100]. Returns None if the plant is not found.
    """
    plant: Plant | None = repo.session.get(Plant, plant_id)
    if plant is None:
        return None

    cutoff = int(time.time()) - 90 * 86400
    # Assignment-aware: include only readings that belonged to this plant at
    # reading time. A sensor that was on this plant 30 days ago and is now on
    # a different one still contributes its 30-days-ago readings; readings
    # taken after the reassignment do not.
    readings = repo.readings_for_plant(plant_id, since_ts=cutoff)
    if not readings:
        return PlantHealthTimelineResponse(plant_id=plant_id, points=[])

    daily_buckets: dict[int, list[float]] = defaultdict(list)
    for r in readings:
        if r.soil_moisture is None:
            continue
        dt = datetime.fromtimestamp(r.timestamp, tz=UTC)
        day_start = int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        daily_buckets[day_start].append(float(r.soil_moisture))

    points = sorted((day_ts, min(100.0, max(0.0, sum(v) / len(v)))) for day_ts, v in daily_buckets.items())

    return PlantHealthTimelineResponse(plant_id=plant_id, points=points)
