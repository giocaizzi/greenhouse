"""Sensor web routes: list, create form, create, edit, delete."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from greenhouse_server.deps import RepoDep, require_cluster, require_sensor_in_cluster
from greenhouse_server.services import inventory
from greenhouse_server.services.inventory import DeviceIdExistsError, PlantNotInClusterError
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


def _parse_optional_plant_id(plant_id: str) -> int | None:
    plant_id = plant_id.strip()
    if not plant_id:
        return None
    try:
        return int(plant_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid plant_id") from exc


@router.get("/clusters/{cluster_id}/sensors")
def list_sensors(cluster_id: int, repo: RepoDep):
    """Redirect the legacy sensors URL to the detail page's sensors section (301).

    Sensors are rendered inline on the unified cluster detail page; the
    redirect keeps old bookmarks working.
    """
    require_cluster(repo, cluster_id)
    return RedirectResponse(url=f"/clusters/{cluster_id}#sensors", status_code=301)


@router.get("/clusters/{cluster_id}/sensors/new")
def new_sensor_form(request: Request, cluster_id: int, repo: RepoDep):
    """Render the add-sensor form."""
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
    """Register a sensor from the form and return to the cluster's sensors section."""
    require_cluster(repo, cluster_id)
    pid = _parse_optional_plant_id(plant_id)
    try:
        inventory.create_sensor(
            repo, cluster_id, tuya_device_id=tuya_device_id, name=name, sensor_type=type, config={}, plant_id=pid
        )
    except PlantNotInClusterError as exc:
        raise HTTPException(404, f"Plant {exc.plant_id} not found in cluster") from None
    except DeviceIdExistsError:
        raise HTTPException(409, "Device ID already exists") from None
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#sensors", status_code=303)


@router.get("/clusters/{cluster_id}/sensors/{sensor_id}/edit")
def edit_sensor_form(request: Request, cluster_id: int, sensor_id: int, repo: RepoDep):
    """Render the edit form of one of the cluster's sensors."""
    cluster = require_cluster(repo, cluster_id)
    sensor = require_sensor_in_cluster(repo, cluster_id, sensor_id)
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
    """Save the sensor form and return to the cluster's sensors section."""
    require_sensor_in_cluster(repo, cluster_id, sensor_id)
    pid = _parse_optional_plant_id(plant_id)
    try:
        inventory.ensure_plant_in_cluster(repo, cluster_id, pid)
    except PlantNotInClusterError as exc:
        raise HTTPException(404, f"Plant {exc.plant_id} not found in cluster") from None
    # update_sensor routes plant_id changes through the assignment-history-aware
    # path. Pass plant_id explicitly even when None so an empty form unassigns.
    repo.update_sensor(sensor_id, name=name, type=type, plant_id=pid)
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#sensors", status_code=303)


@router.delete("/clusters/{cluster_id}/sensors/{sensor_id}", response_class=HTMLResponse)
def delete_sensor(cluster_id: int, sensor_id: int, repo: RepoDep):
    """HTMX-targeted delete; returns an empty HTML body so the row is removed."""
    require_sensor_in_cluster(repo, cluster_id, sensor_id)
    repo.delete_sensor(sensor_id)
    repo.commit()
    return HTMLResponse("")
