"""Irrigator web routes: list, create form, create, start/stop/log-manual."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from greenhouse_server.deps import DeviceManagerDep, RepoDep, require_cluster
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/clusters/{cluster_id}/irrigators")
def list_irrigators(request: Request, cluster_id: int, repo: RepoDep):
    cluster = require_cluster(repo, cluster_id)
    irrigators = repo.get_irrigators_in_cluster(cluster_id)
    return templates.TemplateResponse(
        request, "irrigators/list.html", base_context(request, cluster=cluster, irrigators=irrigators)
    )


@router.get("/clusters/{cluster_id}/irrigators/new")
def new_irrigator_form(request: Request, cluster_id: int, repo: RepoDep):
    cluster = require_cluster(repo, cluster_id)
    return templates.TemplateResponse(request, "irrigators/new.html", base_context(request, cluster=cluster))


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
):
    require_cluster(repo, cluster_id)
    config: dict = {}
    if device_ip.strip():
        config["device_ip"] = device_ip.strip()
    if local_key.strip():
        config["local_key"] = local_key.strip()
    repo.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id=tuya_device_id,
        name=name,
        irrigator_type=type,
        config=config,
    )
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}/irrigators", status_code=303)


def _get_irrigator_or_404(repo: RepoDep, irrigator_id: int):
    irr = repo.get_irrigator(irrigator_id)
    if not irr:
        raise HTTPException(404, "Irrigator not found")
    return irr


@router.post("/irrigators/{irrigator_id}/start")
def start_irrigator(
    request: Request,
    irrigator_id: int,
    repo: RepoDep,
    dm: DeviceManagerDep,
    minutes: str = Form(""),
):
    irr = _get_irrigator_or_404(repo, irrigator_id)
    if dm is None:
        raise HTTPException(503, "No device manager configured")
    mins: int | None = None
    if minutes.strip():
        try:
            mins = int(minutes)
        except ValueError as exc:
            raise HTTPException(400, "Invalid minutes") from exc

    success, message = dm.irrigator_start(irr, mins)
    repo.add_irrigation_event(
        irrigator_id=irr.id,
        action="start" if success else "attempted",
        duration_minutes=mins,
        triggered_by="manual",
        notes=message,
    )
    repo.session.commit()
    return templates.TemplateResponse(
        request,
        "partials/_irrigator_action_result.html",
        base_context(request, success=success, message=message, action="start"),
    )


@router.post("/irrigators/{irrigator_id}/stop")
def stop_irrigator(
    request: Request,
    irrigator_id: int,
    repo: RepoDep,
    dm: DeviceManagerDep,
):
    irr = _get_irrigator_or_404(repo, irrigator_id)
    if dm is None:
        raise HTTPException(503, "No device manager configured")
    success, message = dm.irrigator_off(irr)
    repo.add_irrigation_event(
        irrigator_id=irr.id,
        action="stop" if success else "attempted",
        triggered_by="manual",
        notes=message,
    )
    repo.session.commit()
    return templates.TemplateResponse(
        request,
        "partials/_irrigator_action_result.html",
        base_context(request, success=success, message=message, action="stop"),
    )


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
    return RedirectResponse(url=f"/clusters/{irr.cluster_id}/irrigators", status_code=303)
