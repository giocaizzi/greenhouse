"""Sensor web routes: list, create form, create, edit, delete."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from greenhouse_server.deps import RepoDep, require_cluster
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


def _get_sensor_in_cluster(repo, cluster_id: int, sensor_id: int):
    sensor = repo.get_sensor(sensor_id)
    if not sensor or sensor.cluster_id != cluster_id:
        raise HTTPException(404, "Sensor not found in cluster")
    return sensor


def _parse_optional_plant_id(plant_id: str) -> int | None:
    plant_id = plant_id.strip()
    if not plant_id:
        return None
    try:
        return int(plant_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid plant_id") from exc


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
    pid = _parse_optional_plant_id(plant_id)
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


@router.get("/clusters/{cluster_id}/sensors/{sensor_id}/edit")
def edit_sensor_form(request: Request, cluster_id: int, sensor_id: int, repo: RepoDep):
    cluster = require_cluster(repo, cluster_id)
    sensor = _get_sensor_in_cluster(repo, cluster_id, sensor_id)
    plants = repo.get_plants_in_cluster(cluster_id)
    return templates.TemplateResponse(
        request, "sensors/edit.html", base_context(request, cluster=cluster, sensor=sensor, plants=plants)
    )


@router.post("/clusters/{cluster_id}/sensors/{sensor_id}/edit")
def update_sensor(
    request: Request,
    cluster_id: int,
    sensor_id: int,
    repo: RepoDep,
    name: str = Form(...),
    type: str = Form(...),
    plant_id: str = Form(""),
):
    _get_sensor_in_cluster(repo, cluster_id, sensor_id)
    pid = _parse_optional_plant_id(plant_id)
    # update_sensor routes plant_id changes through the assignment-history-aware
    # path. Pass plant_id explicitly even when None so an empty form unassigns.
    repo.update_sensor(sensor_id, name=name, type=type, plant_id=pid)
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}/sensors", status_code=303)


@router.delete("/clusters/{cluster_id}/sensors/{sensor_id}", response_class=HTMLResponse)
def delete_sensor(cluster_id: int, sensor_id: int, repo: RepoDep):
    """HTMX-targeted delete; returns an empty HTML body so the row is removed."""
    _get_sensor_in_cluster(repo, cluster_id, sensor_id)
    repo.delete_sensor(sensor_id)
    repo.session.commit()
    return HTMLResponse("")
