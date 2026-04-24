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
    if metric not in {"soil_moisture", "temperature", "light", "env_humidity"}:
        raise HTTPException(400, f"Unsupported metric: {metric}")
    payload = build_cluster_chart_payload(repo, plant_db, cluster_id, hours, metric)  # type: ignore[arg-type]
    if not payload:
        raise HTTPException(404, "Cluster not found")
    return payload
