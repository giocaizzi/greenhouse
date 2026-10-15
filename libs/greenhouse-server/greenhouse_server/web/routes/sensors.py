"""Sensor web routes: list, create form, create, edit, move, archive, restore, purge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from greenhouse_core.repository import InvalidMoveTimeError, NotArchivedError
from greenhouse_server.deps import DeviceRegistryDep, RepoDep, require_cluster, require_sensor_in_cluster
from greenhouse_server.services import inventory
from greenhouse_server.services.inventory import DeviceIdExistsError, PlantNotInClusterError
from greenhouse_server.web.context import base_context
from greenhouse_server.web.forms import device_type_options, local_datetime_or_400, parsed_or_400
from greenhouse_server.web.templating import templates

if TYPE_CHECKING:
    from greenhouse_core.devices import DeviceRegistry

router = APIRouter(include_in_schema=False)


# Each form class bundles one route's path parameters and form fields, in the route's order
# (FastAPI builds it as a dependency from the same declarations: parsing and errors unchanged).


@dataclass(frozen=True, slots=True)
class _NewSensorForm:
    """``POST /clusters/{cluster_id}/sensors``: the add form."""

    cluster_id: Annotated[int, Path()]
    tuya_device_id: Annotated[str, Form()]
    name: Annotated[str, Form()]
    type: Annotated[str, Form()]
    plant_id: Annotated[str, Form()] = ""


@dataclass(frozen=True, slots=True)
class _EditSensorForm:
    """``POST /clusters/{cluster_id}/sensors/{sensor_id}/edit``: the edit form (blank plant: unassign)."""

    cluster_id: Annotated[int, Path()]
    sensor_id: Annotated[int, Path()]
    name: Annotated[str, Form()]
    type: Annotated[str, Form()]
    plant_id: Annotated[str, Form()] = ""


@dataclass(frozen=True, slots=True)
class _MoveSensorForm:
    """``POST /clusters/{cluster_id}/sensors/{sensor_id}/move``: target plant (blank: detach) and when."""

    cluster_id: Annotated[int, Path()]
    sensor_id: Annotated[int, Path()]
    plant_id: Annotated[str, Form()] = ""
    effective_at: Annotated[str, Form()] = ""


def _parse_optional_plant_id(plant_id: str) -> int | None:
    return parsed_or_400(plant_id, int, error="Invalid plant_id")


def _type_options(registry: DeviceRegistry | None, current: str | None = None) -> list[tuple[str, str]]:
    """The sensor type ``<select>`` options: the registry's model keys (plus a stored unknown one)."""
    return device_type_options(inventory.known_device_types(registry, "sensor"), current)


def _require_known_type(registry: DeviceRegistry | None, sensor_type: str, stored: str) -> None:
    """422 error page (the API's status and wording) for a changed type no adapter handles."""
    try:
        inventory.require_known_device_type(registry, "sensor", sensor_type, stored=stored)
    except inventory.UnknownDeviceTypeError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/clusters/{cluster_id}/sensors")
def list_sensors(cluster_id: int, repo: RepoDep) -> Response:
    """Redirect the legacy sensors URL to the detail page's sensors section (301).

    Sensors are rendered inline on the unified cluster detail page; the
    redirect keeps old bookmarks working.
    """
    require_cluster(repo, cluster_id)
    return RedirectResponse(url=f"/clusters/{cluster_id}#sensors", status_code=301)


@router.get("/clusters/{cluster_id}/sensors/new")
def new_sensor_form(request: Request, cluster_id: int, repo: RepoDep, registry: DeviceRegistryDep) -> Response:
    """Render the add-sensor form."""
    cluster = require_cluster(repo, cluster_id)
    plants = repo.get_plants_in_cluster(cluster_id)
    return templates.TemplateResponse(
        request,
        "sensors/new.html",
        base_context(request, cluster=cluster, plants=plants, type_options=_type_options(registry)),
    )


@router.post("/clusters/{cluster_id}/sensors")
def create_sensor(repo: RepoDep, registry: DeviceRegistryDep, form: Annotated[_NewSensorForm, Depends()]) -> Response:
    """Register a sensor from the form and return to the cluster's sensors section."""
    cluster_id = form.cluster_id
    require_cluster(repo, cluster_id)
    pid = _parse_optional_plant_id(form.plant_id)
    try:
        inventory.create_sensor(
            repo,
            cluster_id,
            inventory.DeviceSpec(form.tuya_device_id, form.name, form.type, {}),
            registry=registry,
            plant_id=pid,
        )
    except inventory.UnknownDeviceTypeError as exc:
        raise HTTPException(422, str(exc)) from None
    except PlantNotInClusterError as exc:
        raise HTTPException(404, f"Plant {exc.plant_id} not found in cluster") from None
    except DeviceIdExistsError:
        raise HTTPException(409, "Device ID already exists") from None
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#sensors", status_code=303)


@router.get("/clusters/{cluster_id}/sensors/{sensor_id}/edit")
def edit_sensor_form(
    request: Request, cluster_id: int, sensor_id: int, repo: RepoDep, registry: DeviceRegistryDep
) -> Response:
    """Render the edit form of one of the cluster's sensors."""
    cluster = require_cluster(repo, cluster_id)
    sensor = require_sensor_in_cluster(repo, cluster_id, sensor_id)
    plants = repo.get_plants_in_cluster(cluster_id)
    return templates.TemplateResponse(
        request,
        "sensors/edit.html",
        base_context(
            request, cluster=cluster, sensor=sensor, plants=plants, type_options=_type_options(registry, sensor.type)
        ),
    )


@router.post("/clusters/{cluster_id}/sensors/{sensor_id}/edit")
def update_sensor(repo: RepoDep, registry: DeviceRegistryDep, form: Annotated[_EditSensorForm, Depends()]) -> Response:
    """Save the sensor form and return to the cluster's sensors section."""
    cluster_id, sensor_id = form.cluster_id, form.sensor_id
    sensor = require_sensor_in_cluster(repo, cluster_id, sensor_id)
    pid = _parse_optional_plant_id(form.plant_id)
    _require_known_type(registry, form.type, sensor.type)
    try:
        inventory.ensure_plant_in_cluster(repo, cluster_id, pid)
    except PlantNotInClusterError as exc:
        raise HTTPException(404, f"Plant {exc.plant_id} not found in cluster") from None
    # update_sensor routes plant_id changes through the assignment-history-aware
    # path. Pass plant_id explicitly even when None so an empty form unassigns.
    repo.update_sensor(sensor_id, name=form.name, type=form.type, plant_id=pid)
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#sensors", status_code=303)


@router.post("/clusters/{cluster_id}/sensors/{sensor_id}/move")
def move_sensor(repo: RepoDep, form: Annotated[_MoveSensorForm, Depends()]) -> Response:
    """Record a dated sensor move (the API's rules and wording) and return to the sensors section."""
    cluster_id, sensor_id = form.cluster_id, form.sensor_id
    require_sensor_in_cluster(repo, cluster_id, sensor_id)
    pid = _parse_optional_plant_id(form.plant_id)
    when = local_datetime_or_400(form.effective_at, error="Invalid effective_at: use the date and time picker")
    try:
        inventory.move_sensor(repo, cluster_id, sensor_id, pid, effective_at=when)
    except PlantNotInClusterError as exc:
        raise HTTPException(404, f"Plant {exc.plant_id} not found in cluster") from None
    except InvalidMoveTimeError as exc:
        raise HTTPException(400, f"effective_at: {exc}") from None
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#sensors", status_code=303)


@router.delete("/clusters/{cluster_id}/sensors/{sensor_id}", response_class=HTMLResponse)
def delete_sensor(
    cluster_id: int, sensor_id: int, repo: RepoDep, *, purge: Annotated[bool, Query()] = False
) -> Response:
    """HTMX-targeted delete: archive the sensor, or ``purge`` an archived one; empty body drops the row."""
    require_sensor_in_cluster(repo, cluster_id, sensor_id)
    try:
        inventory.remove_sensor(repo, sensor_id, purge=purge)
    except NotArchivedError as exc:
        raise HTTPException(409, str(exc)) from None
    repo.commit()
    return HTMLResponse("")


@router.post("/clusters/{cluster_id}/sensors/{sensor_id}/restore")
def restore_sensor(cluster_id: int, sensor_id: int, repo: RepoDep) -> Response:
    """Bring an archived sensor back (re-linked to its plant if still active) and return to the sensors section."""
    require_sensor_in_cluster(repo, cluster_id, sensor_id)
    repo.restore_sensor(sensor_id)
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#sensors", status_code=303)
