"""API endpoints for Chart.js payload data and single-plant lookup."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from tuya_irrigation_core.models import Plant
from tuya_irrigation_core.schemas import ChartPayloadResponse, PlantResponse
from tuya_irrigation_server.deps import PlantDbDep, RepoDep
from tuya_irrigation_server.services.charts import build_cluster_chart_payload, build_plant_chart_payload

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
