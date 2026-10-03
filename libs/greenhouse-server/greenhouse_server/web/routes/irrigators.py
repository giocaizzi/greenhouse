"""Irrigator web routes: list, create form, create, edit, delete, start/stop/log-manual."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

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
from greenhouse_server.services.manual_control import ManualActionError, manual_log, manual_start, manual_stop
from greenhouse_server.web.context import base_context
from greenhouse_server.web.forms import parsed_or_400
from greenhouse_server.web.templating import templates

if TYPE_CHECKING:
    from greenhouse_core.models import Cluster

router = APIRouter(include_in_schema=False)


@router.get("/clusters/{cluster_id}/irrigators")
def list_irrigators(cluster_id: int, repo: RepoDep):
    """Redirect the legacy irrigators URL to the detail page's irrigators section (301).

    Irrigators are rendered inline on the unified cluster detail page; the
    redirect keeps old bookmarks working.
    """
    require_cluster(repo, cluster_id)
    return RedirectResponse(url=f"/clusters/{cluster_id}#irrigators", status_code=301)


@router.get("/clusters/{cluster_id}/irrigators/new")
def new_irrigator_form(request: Request, cluster_id: int, repo: RepoDep):
    """Render the add-irrigator form, or go back to the cluster when it already has one."""
    cluster = require_cluster(repo, cluster_id)
    # A cluster has at most one irrigator. If one already exists, send the user
    # back to the detail page instead of offering an "add" form they cannot use.
    if repo.get_irrigator_for_cluster(cluster_id) is not None:
        return RedirectResponse(url=f"/clusters/{cluster_id}#irrigators", status_code=303)
    return templates.TemplateResponse(request, "irrigators/new.html", base_context(request, cluster=cluster))


def _parse_capacity(raw: str) -> float | None:
    """Parse an optional non-negative float from a form field; blank -> None."""
    return parsed_or_400(
        raw, float, error="Capacity values must be numbers", negative_error="Capacity values must be >= 0"
    )


def _new_form_conflict(request: Request, cluster: Cluster, error: str) -> HTMLResponse:
    """Re-render the add form with the conflict message (HTTP 409), as the API's 409."""
    return templates.TemplateResponse(
        request, "irrigators/new.html", base_context(request, cluster=cluster, error=error), status_code=409
    )


@router.post("/clusters/{cluster_id}/irrigators")
def create_irrigator(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    tuya_device_id: str = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    device_ip: str = Form(""),
    local_key: str = Form(""),
    reservoir_l: str = Form(""),
    flow_rate_l_per_min: str = Form(""),
):
    """Register the cluster's irrigator from the form; a conflict re-renders the form with 409."""
    cluster = require_cluster(repo, cluster_id)
    config: dict = {}
    if device_ip.strip():
        config["device_ip"] = device_ip.strip()
    if local_key.strip():
        config["local_key"] = local_key.strip()
    reservoir = _parse_capacity(reservoir_l)
    flow_rate = _parse_capacity(flow_rate_l_per_min)
    try:
        inventory.create_irrigator(
            repo,
            cluster_id,
            tuya_device_id=tuya_device_id,
            name=name,
            irrigator_type=type,
            config=config,
            reservoir_l=reservoir,
            flow_rate_l_per_min=flow_rate,
        )
    except IrrigatorExistsError:
        # Re-render the form with a user-facing error instead of a redirect; a
        # cluster may have at most one irrigator.
        return _new_form_conflict(
            request, cluster, "This cluster already has an irrigator. A cluster can have at most one."
        )
    except DeviceIdExistsError:
        return _new_form_conflict(request, cluster, "Device ID already exists")
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#irrigators", status_code=303)


@router.get("/clusters/{cluster_id}/irrigators/edit")
def edit_irrigator_form(request: Request, cluster_id: int, repo: RepoDep):
    """Render the edit form of the cluster's irrigator."""
    cluster = require_cluster(repo, cluster_id)
    irrigator = require_cluster_irrigator(repo, cluster_id)
    config = parse_device_config(irrigator.config)
    return templates.TemplateResponse(
        request,
        "irrigators/edit.html",
        base_context(request, cluster=cluster, irrigator=irrigator, config=config),
    )


@router.post("/clusters/{cluster_id}/irrigators/edit")
def update_irrigator(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    name: str = Form(...),
    type: str = Form(...),
    device_ip: str = Form(""),
    local_key: str = Form(""),
    reservoir_l: str = Form(""),
    flow_rate_l_per_min: str = Form(""),
):
    """Save the irrigator form (blank connection fields keep their stored values)."""
    irrigator = require_cluster_irrigator(repo, cluster_id)
    # Merge into the stored config so a blank field PRESERVES the current value
    # rather than wiping it. The local key is a root-level credential — a blank
    # submit must never silently erase it (the form intentionally renders it
    # masked and empty, so most saves arrive blank).
    config = parse_device_config(irrigator.config)
    if device_ip.strip():
        config["device_ip"] = device_ip.strip()
    if local_key.strip():
        config["local_key"] = local_key.strip()
    repo.update_irrigator(
        irrigator.id,
        name=name,
        type=type,
        config=config,
        reservoir_l=_parse_capacity(reservoir_l),
        flow_rate_l_per_min=_parse_capacity(flow_rate_l_per_min),
    )
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#irrigators", status_code=303)


@router.delete("/clusters/{cluster_id}/irrigators", response_class=HTMLResponse)
def delete_irrigator(cluster_id: int, repo: RepoDep):
    """HTMX-targeted delete; returns an empty HTML body so the row is removed."""
    irrigator = require_cluster_irrigator(repo, cluster_id)
    repo.delete_irrigator(irrigator.id)
    repo.commit()
    return HTMLResponse("")


def _action_result(request: Request, action: str, run) -> HTMLResponse:
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
    irrigator_id: int,
    repo: RepoDep,
    registry: DeviceRegistryDep,
    notifier: NtfyNotifierDep,
    minutes: str = Form(""),
):
    """Manually start an irrigator and render the action result (HTMX fragment)."""
    # Same code path as POST /api/v1/irrigators/{id}/start: caps, dry-run
    # watcher, event row and notification.
    irr = require_irrigator(repo, irrigator_id)
    mins = parsed_or_400(minutes, int, error="Invalid minutes")
    return _action_result(request, "start", lambda: manual_start(repo, registry, notifier, irr, mins, via="web UI"))


@router.post("/irrigators/{irrigator_id}/stop")
def stop_irrigator(
    request: Request,
    irrigator_id: int,
    repo: RepoDep,
    registry: DeviceRegistryDep,
    notifier: NtfyNotifierDep,
):
    """Manually stop an irrigator and render the action result (HTMX fragment)."""
    irr = require_irrigator(repo, irrigator_id)
    return _action_result(request, "stop", lambda: manual_stop(repo, registry, notifier, irr, via="web UI"))


@router.get("/irrigators/{irrigator_id}/log-manual")
def log_manual_form(request: Request, irrigator_id: int, repo: RepoDep):
    """Render the form that records a manual watering for an irrigator."""
    irr = require_irrigator(repo, irrigator_id)
    return templates.TemplateResponse(request, "irrigators/log_manual.html", base_context(request, irrigator=irr))


@router.post("/irrigators/{irrigator_id}/log-manual")
def log_manual_submit(
    request: Request,
    irrigator_id: int,
    repo: RepoDep,
    notifier: NtfyNotifierDep,
    minutes: int = Form(...),
    notes: str = Form(""),
):
    """Record a manual watering; a refusal re-renders the form with the error."""
    irr = require_irrigator(repo, irrigator_id)
    try:
        manual_log(repo, notifier, irr, minutes, notes or None)
    except ManualActionError as e:
        return templates.TemplateResponse(
            request,
            "irrigators/log_manual.html",
            base_context(request, irrigator=irr, error=e.detail, minutes=minutes, notes=notes),
            status_code=e.status_code,
        )
    return RedirectResponse(url=f"/clusters/{irr.cluster_id}#irrigators", status_code=303)
