"""Sensor web routes: list, create form, create."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from tuya_irrigation_server.deps import RepoDep, require_cluster
from tuya_irrigation_server.web.context import base_context
from tuya_irrigation_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/clusters/{cluster_id}/sensors")
def list_sensors(request: Request, cluster_id: int, repo: RepoDep):
    cluster = require_cluster(repo, cluster_id)
    sensors = repo.get_sensors_in_cluster(cluster_id)
    plants = {p.id: p for p in repo.get_plants_in_cluster(cluster_id)}
    return templates.TemplateResponse(
        request, "sensors/list.html", base_context(request, cluster=cluster, sensors=sensors, plants=plants)
    )


@router.get("/clusters/{cluster_id}/sensors/new")
def new_sensor_form(request: Request, cluster_id: int, repo: RepoDep):
    cluster = require_cluster(repo, cluster_id)
    plants = repo.get_plants_in_cluster(cluster_id)
    return templates.TemplateResponse(
        request, "sensors/new.html", base_context(request, cluster=cluster, plants=plants)
    )


@router.post("/clusters/{cluster_id}/sensors")
def create_sensor(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    tuya_device_id: str = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    plant_id: str = Form(""),
):
    require_cluster(repo, cluster_id)
    pid: int | None = None
    if plant_id.strip():
        try:
            pid = int(plant_id)
        except ValueError as exc:
            raise HTTPException(400, "Invalid plant_id") from exc
    repo.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id=tuya_device_id,
        name=name,
        sensor_type=type,
        config={},
        plant_id=pid,
    )
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}/sensors", status_code=303)
