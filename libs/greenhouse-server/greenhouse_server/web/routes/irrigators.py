"""Irrigator web routes: list, create form, create, edit, delete, start/stop/log-manual."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from greenhouse_core.models import parse_device_config
from greenhouse_server.deps import (
    DeviceRegistryDep,
    NtfyNotifierDep,
    RepoDep,
    require_cluster,
    require_cluster_irrigator,
    require_irrigator,
)
from greenhouse_server.services import inventory
from greenhouse_server.services.inventory import DeviceIdExistsError, IrrigatorExistsError
from greenhouse_server.services.manual_control import (
    ManualActionError,
    ManualStart,
    manual_log,
    manual_start,
    manual_stop,
)
from greenhouse_server.web.context import base_context
from greenhouse_server.web.forms import device_type_options, parsed_or_400
from greenhouse_server.web.templating import templates

if TYPE_CHECKING:
    from collections.abc import Callable

    from greenhouse_core.devices import DeviceRegistry
    from greenhouse_core.models import Cluster

router = APIRouter(include_in_schema=False)


# Each form class bundles one route's path parameter and form fields (FastAPI builds it as a
# dependency from the same ``Path`` / ``Form`` declarations, so parsing and errors are unchanged).


def _connection_config(stored: dict[str, Any], device_ip: str, local_key: str) -> dict[str, Any]:
    """``stored`` with the non-blank connection fields applied (a blank field keeps its value)."""
    config = dict(stored)
    if device_ip.strip():
        config["device_ip"] = device_ip.strip()
    if local_key.strip():
        config["local_key"] = local_key.strip()
    return config


@dataclass(frozen=True, slots=True)
class _NewIrrigatorForm:
    """``POST /clusters/{cluster_id}/irrigators``: the add form."""

    cluster_id: Annotated[int, Path()]
    tuya_device_id: Annotated[str, Form()]
    name: Annotated[str, Form()]
    type: Annotated[str, Form()]
    device_ip: Annotated[str, Form()] = ""
    local_key: Annotated[str, Form()] = ""
    reservoir_l: Annotated[str, Form()] = ""
    flow_rate_l_per_min: Annotated[str, Form()] = ""


@dataclass(frozen=True, slots=True)
class _EditIrrigatorForm:
    """``POST /clusters/{cluster_id}/irrigators/edit``: the edit form (the device id is not editable)."""

    cluster_id: Annotated[int, Path()]
    name: Annotated[str, Form()]
    type: Annotated[str, Form()]
    device_ip: Annotated[str, Form()] = ""
    local_key: Annotated[str, Form()] = ""
    reservoir_l: Annotated[str, Form()] = ""
    flow_rate_l_per_min: Annotated[str, Form()] = ""


@dataclass(frozen=True, slots=True)
class _StartForm:
    """``POST /irrigators/{irrigator_id}/start``: an optional duration (blank: the device timer decides)."""

    irrigator_id: Annotated[int, Path()]
    minutes: Annotated[str, Form()] = ""


@dataclass(frozen=True, slots=True)
class _LogManualForm:
    """``POST /irrigators/{irrigator_id}/log-manual``: a hand watering's duration and notes."""

    irrigator_id: Annotated[int, Path()]
    minutes: Annotated[int, Form()]
    notes: Annotated[str, Form()] = ""


@router.get("/clusters/{cluster_id}/irrigators")
def list_irrigators(cluster_id: int, repo: RepoDep) -> Response:
    """Redirect the legacy irrigators URL to the detail page's irrigators section (301).

    Irrigators are rendered inline on the unified cluster detail page; the
    redirect keeps old bookmarks working.
    """
    require_cluster(repo, cluster_id)
    return RedirectResponse(url=f"/clusters/{cluster_id}#irrigators", status_code=301)


@router.get("/clusters/{cluster_id}/irrigators/new")
def new_irrigator_form(request: Request, cluster_id: int, repo: RepoDep, registry: DeviceRegistryDep) -> Response:
    """Render the add-irrigator form, or go back to the cluster when it already has one."""
    cluster = require_cluster(repo, cluster_id)
    # A cluster has at most one irrigator. If one already exists, send the user
    # back to the detail page instead of offering an "add" form they cannot use.
    if repo.get_irrigator_for_cluster(cluster_id) is not None:
        return RedirectResponse(url=f"/clusters/{cluster_id}#irrigators", status_code=303)
    return templates.TemplateResponse(
        request, "irrigators/new.html", base_context(request, cluster=cluster, type_options=_type_options(registry))
    )


def _parse_capacity(raw: str) -> float | None:
    """Parse an optional non-negative float from a form field; blank -> None."""
    return parsed_or_400(
        raw, float, error="Capacity values must be numbers", negative_error="Capacity values must be >= 0"
    )


def _type_options(registry: DeviceRegistry | None, current: str | None = None) -> list[tuple[str, str]]:
    """The irrigator type ``<select>`` options: the registry's model keys (plus a stored unknown one)."""
    return device_type_options(inventory.known_device_types(registry, "irrigator"), current)


def _new_form_error(
    request: Request, cluster: Cluster, registry: DeviceRegistry | None, error: str, status_code: int
) -> HTMLResponse:
    """Re-render the add form with the error message, with the API's status (409 conflict, 422 unknown type)."""
    context = base_context(request, cluster=cluster, error=error, type_options=_type_options(registry))
    return templates.TemplateResponse(request, "irrigators/new.html", context, status_code=status_code)


@router.post("/clusters/{cluster_id}/irrigators")
def create_irrigator(
    request: Request, repo: RepoDep, registry: DeviceRegistryDep, form: Annotated[_NewIrrigatorForm, Depends()]
) -> Response:
    """Register the cluster's irrigator from the form; a conflict re-renders the form with 409."""
    cluster_id = form.cluster_id
    cluster = require_cluster(repo, cluster_id)
    config = _connection_config({}, form.device_ip, form.local_key)
    reservoir = _parse_capacity(form.reservoir_l)
    flow_rate = _parse_capacity(form.flow_rate_l_per_min)
    try:
        inventory.create_irrigator(
            repo,
            cluster_id,
            inventory.DeviceSpec(form.tuya_device_id, form.name, form.type, config),
            registry=registry,
            capacity=inventory.IrrigatorCapacity(reservoir, flow_rate),
        )
    except IrrigatorExistsError:
        # Re-render the form with a user-facing error instead of a redirect; a
        # cluster may have at most one irrigator.
        return _new_form_error(
            request, cluster, registry, "This cluster already has an irrigator. A cluster can have at most one.", 409
        )
    except DeviceIdExistsError:
        return _new_form_error(request, cluster, registry, "Device ID already exists", 409)
    except inventory.UnknownDeviceTypeError as exc:
        return _new_form_error(request, cluster, registry, str(exc), 422)
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#irrigators", status_code=303)


@router.get("/clusters/{cluster_id}/irrigators/edit")
def edit_irrigator_form(request: Request, cluster_id: int, repo: RepoDep, registry: DeviceRegistryDep) -> Response:
    """Render the edit form of the cluster's irrigator."""
    cluster = require_cluster(repo, cluster_id)
    irrigator = require_cluster_irrigator(repo, cluster_id)
    config = parse_device_config(irrigator.config)
    return templates.TemplateResponse(
        request,
        "irrigators/edit.html",
        base_context(
            request,
            cluster=cluster,
            irrigator=irrigator,
            config=config,
            type_options=_type_options(registry, irrigator.type),
        ),
    )


@router.post("/clusters/{cluster_id}/irrigators/edit")
def update_irrigator(
    repo: RepoDep, registry: DeviceRegistryDep, form: Annotated[_EditIrrigatorForm, Depends()]
) -> Response:
    """Save the irrigator form (blank connection fields keep their stored values)."""
    irrigator = require_cluster_irrigator(repo, form.cluster_id)
    try:
        inventory.require_known_device_type(registry, "irrigator", form.type, stored=irrigator.type)
    except inventory.UnknownDeviceTypeError as exc:
        raise HTTPException(422, str(exc)) from None
    # Merge into the stored config so a blank field PRESERVES the current value
    # rather than wiping it. The local key is a root-level credential — a blank
    # submit must never silently erase it (the form intentionally renders it
    # masked and empty, so most saves arrive blank).
    config = _connection_config(parse_device_config(irrigator.config), form.device_ip, form.local_key)
    # The form shows the mask as its placeholder; a pasted mask means "keep", as on the API.
    config = inventory.keep_masked_device_secrets(config, irrigator.config)
    repo.update_irrigator(
        irrigator.id,
        name=form.name,
        type=form.type,
        config=config,
        reservoir_l=_parse_capacity(form.reservoir_l),
        flow_rate_l_per_min=_parse_capacity(form.flow_rate_l_per_min),
    )
    repo.commit()
    return RedirectResponse(url=f"/clusters/{form.cluster_id}#irrigators", status_code=303)


@router.delete("/clusters/{cluster_id}/irrigators", response_class=HTMLResponse)
def delete_irrigator(cluster_id: int, repo: RepoDep) -> Response:
    """HTMX-targeted delete; returns an empty HTML body so the row is removed."""
    irrigator = require_cluster_irrigator(repo, cluster_id)
    repo.delete_irrigator(irrigator.id)
    repo.commit()
    return HTMLResponse("")


def _action_result(request: Request, action: str, run: Callable[[], str]) -> HTMLResponse:
    """Run a shared manual action and render the HX result partial.

    Refusals the JSON API reports as 409 (caps) / 502 (device failure) are
    rendered as a failed result in the action slot, as device failures always
    were; missing registry / unknown model stay HTTP 503.
    """
    try:
        success, message = True, run()
    except ManualActionError as exc:
        if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            raise HTTPException(exc.status_code, exc.detail) from exc
        success, message = False, exc.detail
    return templates.TemplateResponse(
        request,
        "partials/_irrigator_action_result.html",
        base_context(request, success=success, message=message, action=action),
    )


@router.post("/irrigators/{irrigator_id}/start")
def start_irrigator(
    request: Request,
    repo: RepoDep,
    registry: DeviceRegistryDep,
    notifier: NtfyNotifierDep,
    form: Annotated[_StartForm, Depends()],
) -> Response:
    """Manually start an irrigator and render the action result (HTMX fragment)."""
    # Same code path as POST /api/v1/irrigators/{id}/start: caps, dry-run
    # watcher, event row and notification.
    irr = require_irrigator(repo, form.irrigator_id)
    mins = parsed_or_400(form.minutes, int, error="Invalid minutes")
    return _action_result(
        request, "start", lambda: manual_start(repo, registry, notifier, irr, ManualStart(mins, via="web UI"))
    )


@router.post("/irrigators/{irrigator_id}/stop")
def stop_irrigator(
    request: Request,
    irrigator_id: int,
    repo: RepoDep,
    registry: DeviceRegistryDep,
    notifier: NtfyNotifierDep,
) -> Response:
    """Manually stop an irrigator and render the action result (HTMX fragment)."""
    irr = require_irrigator(repo, irrigator_id)
    return _action_result(request, "stop", lambda: manual_stop(repo, registry, notifier, irr, via="web UI"))


@router.get("/irrigators/{irrigator_id}/log-manual")
def log_manual_form(request: Request, irrigator_id: int, repo: RepoDep) -> Response:
    """Render the form that records a manual watering for an irrigator."""
    irr = require_irrigator(repo, irrigator_id)
    return templates.TemplateResponse(request, "irrigators/log_manual.html", base_context(request, irrigator=irr))


@router.post("/irrigators/{irrigator_id}/log-manual")
def log_manual_submit(
    request: Request, repo: RepoDep, notifier: NtfyNotifierDep, form: Annotated[_LogManualForm, Depends()]
) -> Response:
    """Record a manual watering; a refusal re-renders the form with the error."""
    irr = require_irrigator(repo, form.irrigator_id)
    try:
        manual_log(repo, notifier, irr, form.minutes, form.notes or None)
    except ManualActionError as e:
        return templates.TemplateResponse(
            request,
            "irrigators/log_manual.html",
            base_context(request, irrigator=irr, error=e.detail, minutes=form.minutes, notes=form.notes),
            status_code=e.status_code,
        )
    return RedirectResponse(url=f"/clusters/{irr.cluster_id}#irrigators", status_code=303)
