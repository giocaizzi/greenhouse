"""Chart payload builder shared by API and web routes."""

from __future__ import annotations

import time
from typing import Literal

from tuya_irrigation_core.constants import DEFAULT_SOIL_MOISTURE_MAX, DEFAULT_SOIL_MOISTURE_MIN
from tuya_irrigation_core.models import Plant, Sensor
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository

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

    sensors = [s for s in repo.get_sensors_in_cluster(plant.cluster_id) if s.plant_id == plant_id]
    datasets = _build_sensor_datasets(repo, sensors, hours, metric)
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
    irrigators = repo.get_irrigators_in_cluster(cluster_id)
    cutoff = int(time.time()) - (hours * 3600)
    events: list[dict] = []
    for irr in irrigators:
        for e in repo.get_recent_events(irr.id, hours=hours):
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
