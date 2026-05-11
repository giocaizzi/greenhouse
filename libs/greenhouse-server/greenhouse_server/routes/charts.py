"""API endpoints for Chart.js payload data and single-plant lookup."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from greenhouse_core.models import Plant
from greenhouse_core.schemas import (
    ChartPayloadResponse,
    HeatmapResponse,
    MultiMetricOverlayResponse,
    PlantHealthTimelineResponse,
    PlantResponse,
)
from greenhouse_server.deps import PlantDbDep, RepoDep
from greenhouse_server.services.charts import (
    build_cluster_chart_payload,
    build_heatmap_payload,
    build_overlay_payload,
    build_plant_chart_payload,
    build_plant_health_timeline_payload,
)

router = APIRouter(tags=["charts"])


@router.get("/plants/{plant_id}", response_model=PlantResponse)
def get_plant(plant_id: int, repo: RepoDep):
    """Fetch a single plant by ID across all clusters.

    Args:
        plant_id: Numeric plant identifier.

    Raises:
        HTTPException: 404 if no plant with that ID exists.
    """
    plant: Plant | None = repo.session.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(404, "Plant not found")
    return plant


@router.get("/plants/{plant_id}/chart-data", response_model=ChartPayloadResponse)
def plant_chart_data(
    plant_id: int,
    repo: RepoDep,
    plant_db: PlantDbDep,
    hours: int = Query(24, ge=1, le=8760),
    metric: str = Query("soil_moisture"),
):
    """Time-series chart payload for a single plant.

    Returns sensor readings, irrigation events, and the plant-care threshold
    band so the caller can render a Chart.js time-scale chart.

    Args:
        plant_id: Plant whose sensors to chart.
        hours: Look-back window (1–8760).
        metric: One of `soil_moisture`, `temperature`, `light`, `env_humidity`.

    Raises:
        HTTPException: 400 if the metric is unsupported, 404 if the plant
            does not exist.
    """
    if metric not in {"soil_moisture", "temperature", "light", "env_humidity"}:
        raise HTTPException(400, f"Unsupported metric: {metric}")
    payload = build_plant_chart_payload(repo, plant_db, plant_id, hours, metric)  # type: ignore[arg-type]
    if not payload:
        raise HTTPException(404, "Plant not found")
    return payload


@router.get("/clusters/{cluster_id}/chart-data", response_model=ChartPayloadResponse)
def cluster_chart_data(
    cluster_id: int,
    repo: RepoDep,
    plant_db: PlantDbDep,
    hours: int = Query(24, ge=1, le=8760),
    metric: str = Query("soil_moisture"),
):
    """Time-series chart payload aggregated across every sensor in a cluster.

    Args:
        cluster_id: Cluster whose sensors to chart.
        hours: Look-back window (1–8760).
        metric: One of `soil_moisture`, `temperature`, `light`, `env_humidity`.

    Raises:
        HTTPException: 400 if the metric is unsupported, 404 if the cluster
            does not exist.
    """
    if metric not in {"soil_moisture", "temperature", "light", "env_humidity"}:
        raise HTTPException(400, f"Unsupported metric: {metric}")
    payload = build_cluster_chart_payload(repo, plant_db, cluster_id, hours, metric)  # type: ignore[arg-type]
    if not payload:
        raise HTTPException(404, "Cluster not found")
    return payload


@router.get("/clusters/{cluster_id}/overlay", response_model=MultiMetricOverlayResponse)
def cluster_overlay(
    cluster_id: int,
    repo: RepoDep,
    hours: int = Query(72, ge=1, le=8760),
):
    """Multi-metric overlay payload with soil moisture, humidity, and light normalised to 0-100.

    All three series share a common Y axis (0-100) so they can be overlaid on one chart.
    Light is rescaled from lux using a 10 000 lx ceiling; the original ceiling is
    returned as `original_max` on the light dataset for tooltip back-conversion.

    Args:
        cluster_id: Cluster to summarise.
        hours: Look-back window (1–8760, default 72).

    Returns:
        MultiMetricOverlayResponse with normalised datasets and irrigation event markers.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    payload = build_overlay_payload(repo, cluster_id, hours)
    if payload is None:
        raise HTTPException(404, "Cluster not found")
    return payload


@router.get("/clusters/{cluster_id}/heatmap", response_model=HeatmapResponse)
def cluster_heatmap(
    cluster_id: int,
    repo: RepoDep,
    days: int = Query(30, ge=1, le=365),
):
    """Irrigation frequency heatmap cells for a 7×24 weekday-by-hour grid.

    Each non-zero cell records the count of irrigation events and the total
    irrigated minutes for that (weekday, hour) combination within the look-back
    window. Zero-count cells are omitted to keep the payload compact.

    Args:
        cluster_id: Cluster to summarise.
        days: Look-back window in days (1–365, default 30).

    Returns:
        HeatmapResponse with a sparse list of HeatmapCell objects.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    payload = build_heatmap_payload(repo, cluster_id, days)
    if payload is None:
        raise HTTPException(404, "Cluster not found")
    return payload


@router.get("/plants/{plant_id}/health-timeline", response_model=PlantHealthTimelineResponse)
def plant_health_timeline(
    plant_id: int,
    repo: RepoDep,
):
    """90-day daily health score timeline for a single plant.

    Health score per day (0–100) is the mean soil moisture across all sensors
    linked to the plant. Points are (unix_timestamp_of_day_start, score) tuples
    in ascending time order. Days with no readings are omitted.

    Args:
        plant_id: Plant to chart.

    Returns:
        PlantHealthTimelineResponse with daily score points and threshold levels.

    Raises:
        HTTPException: 404 if the plant does not exist.
    """
    payload = build_plant_health_timeline_payload(repo, plant_id)
    if payload is None:
        raise HTTPException(404, "Plant not found")
    return payload
