"""Irrigator web routes: list, create form, create."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tuya_irrigation_server.deps import RepoDep, require_cluster
from tuya_irrigation_server.web.context import base_context
from tuya_irrigation_server.web.templating import templates

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
