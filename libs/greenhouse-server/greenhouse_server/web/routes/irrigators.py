"""Irrigator web routes: list, create form, create, edit, delete, start/stop/log-manual."""

from __future__ import annotations

import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from greenhouse_core.repository import IrrigatorExistsError
from greenhouse_server.deps import DeviceRegistryDep, NtfyNotifierDep, RepoDep, require_cluster
from greenhouse_server.services.manual_control import ManualActionError, manual_start, manual_stop
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


def _require_cluster_irrigator(repo, cluster_id: int):
    irr = repo.get_irrigator_for_cluster(cluster_id)
    if not irr:
        raise HTTPException(404, "Cluster has no irrigator")
    return irr


def _parse_config(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw is None or raw == "":
        return {}
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


@router.get("/clusters/{cluster_id}/irrigators")
def list_irrigators(cluster_id: int, repo: RepoDep):
    """Legacy URL — irrigators are rendered inline on the unified cluster
    detail page. The 301 keeps old bookmarks working."""
    require_cluster(repo, cluster_id)
    return RedirectResponse(url=f"/clusters/{cluster_id}#irrigators", status_code=301)


@router.get("/clusters/{cluster_id}/irrigators/new")
def new_irrigator_form(request: Request, cluster_id: int, repo: RepoDep):
    cluster = require_cluster(repo, cluster_id)
    # A cluster has at most one irrigator. If one already exists, send the user
    # back to the detail page instead of offering an "add" form they cannot use.
    if repo.get_irrigator_for_cluster(cluster_id) is not None:
        return RedirectResponse(url=f"/clusters/{cluster_id}#irrigators", status_code=303)
    return templates.TemplateResponse(request, "irrigators/new.html", base_context(request, cluster=cluster))


def _parse_capacity(raw: str) -> float | None:
    """Parse an optional non-negative float from a form field; blank -> None."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise HTTPException(400, "Capacity values must be numbers") from exc
    if value < 0:
        raise HTTPException(400, "Capacity values must be >= 0")
    return value


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
    cluster = require_cluster(repo, cluster_id)
    config: dict = {}
    if device_ip.strip():
        config["device_ip"] = device_ip.strip()
    if local_key.strip():
        config["local_key"] = local_key.strip()
    reservoir = _parse_capacity(reservoir_l)
    flow_rate = _parse_capacity(flow_rate_l_per_min)
    try:
        irrigator_id = repo.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=tuya_device_id,
            name=name,
            irrigator_type=type,
            config=config,
        )
    except IrrigatorExistsError:
        repo.session.rollback()
        # Re-render the form with a user-facing error instead of a redirect; a
        # cluster may have at most one irrigator.
        return templates.TemplateResponse(
            request,
            "irrigators/new.html",
            base_context(
                request,
                cluster=cluster,
                error="This cluster already has an irrigator. A cluster can have at most one.",
            ),
            status_code=409,
        )
    if reservoir is not None or flow_rate is not None:
        repo.update_irrigator(irrigator_id, reservoir_l=reservoir, flow_rate_l_per_min=flow_rate)
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#irrigators", status_code=303)


@router.get("/clusters/{cluster_id}/irrigators/edit")
def edit_irrigator_form(request: Request, cluster_id: int, repo: RepoDep):
    cluster = require_cluster(repo, cluster_id)
    irrigator = _require_cluster_irrigator(repo, cluster_id)
    config = _parse_config(irrigator.config)
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
    irrigator = _require_cluster_irrigator(repo, cluster_id)
    # Merge into the stored config so a blank field PRESERVES the current value
    # rather than wiping it. The local key is a root-level credential — a blank
    # submit must never silently erase it (the form intentionally renders it
    # masked and empty, so most saves arrive blank).
    config = _parse_config(irrigator.config)
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
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#irrigators", status_code=303)


@router.delete("/clusters/{cluster_id}/irrigators", response_class=HTMLResponse)
def delete_irrigator(cluster_id: int, repo: RepoDep):
    """HTMX-targeted delete; returns an empty HTML body so the row is removed."""
    irrigator = _require_cluster_irrigator(repo, cluster_id)
    repo.delete_irrigator(irrigator.id)
    repo.session.commit()
    return HTMLResponse("")


def _get_irrigator_or_404(repo: RepoDep, irrigator_id: int):
    irr = repo.get_irrigator(irrigator_id)
    if not irr:
        raise HTTPException(404, "Irrigator not found")
    return irr


def _action_result(request: Request, action: str, run) -> HTMLResponse:
    """Run a shared manual action and render the HX result partial.

    Refusals the JSON API reports as 409 (caps) / 502 (device failure) are
    rendered as a failed result in the action slot, as device failures always
    were; missing registry / unknown model stay HTTP 503.
    """
    try:
        success, message = True, run()
    except ManualActionError as exc:
        if exc.status_code == 503:
            raise HTTPException(503, exc.detail) from exc
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
    # Same code path as POST /api/v1/irrigators/{id}/start: caps, dry-run
    # watcher, event row and notification (the web route used to skip them).
    irr = _get_irrigator_or_404(repo, irrigator_id)
    mins: int | None = None
    if minutes.strip():
        try:
            mins = int(minutes)
        except ValueError as exc:
            raise HTTPException(400, "Invalid minutes") from exc
    return _action_result(request, "start", lambda: manual_start(repo, registry, notifier, irr, mins, via="web UI"))


@router.post("/irrigators/{irrigator_id}/stop")
def stop_irrigator(
    request: Request,
    irrigator_id: int,
    repo: RepoDep,
    registry: DeviceRegistryDep,
    notifier: NtfyNotifierDep,
):
    irr = _get_irrigator_or_404(repo, irrigator_id)
    return _action_result(request, "stop", lambda: manual_stop(repo, registry, notifier, irr, via="web UI"))


@router.get("/irrigators/{irrigator_id}/log-manual")
def log_manual_form(request: Request, irrigator_id: int, repo: RepoDep):
    irr = _get_irrigator_or_404(repo, irrigator_id)
    return templates.TemplateResponse(request, "irrigators/log_manual.html", base_context(request, irrigator=irr))


@router.post("/irrigators/{irrigator_id}/log-manual")
def log_manual_submit(
    request: Request,
    irrigator_id: int,
    repo: RepoDep,
    minutes: int = Form(...),
    notes: str = Form(""),
):
    irr = _get_irrigator_or_404(repo, irrigator_id)
    repo.add_irrigation_event(
        irrigator_id=irr.id,
        action="manual",
        duration_minutes=minutes,
        triggered_by="manual",
        notes=notes or None,
    )
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{irr.cluster_id}#irrigators", status_code=303)
