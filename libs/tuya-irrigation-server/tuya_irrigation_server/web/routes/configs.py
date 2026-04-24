"""Irrigation config web routes: get/edit form, save."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tuya_irrigation_server.deps import RepoDep, require_cluster
from tuya_irrigation_server.web.context import base_context
from tuya_irrigation_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/clusters/{cluster_id}/config")
def config_form(request: Request, cluster_id: int, repo: RepoDep):
    cluster = require_cluster(repo, cluster_id)
    config = repo.get_irrigation_config(cluster_id)
    return templates.TemplateResponse(
        request, "configs/edit.html", base_context(request, cluster=cluster, config=config)
    )


@router.post("/clusters/{cluster_id}/config")
def save_config(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    mode: str = Form(...),
    duration_minutes: str = Form(""),
    interval_hours: str = Form(""),
    auto_run: str = Form(""),
):
    require_cluster(repo, cluster_id)
    dm = int(duration_minutes) if duration_minutes.strip() else None
    ih = int(interval_hours) if interval_hours.strip() else None
    repo.set_irrigation_config(
        cluster_id=cluster_id,
        mode=mode,
        duration_minutes=dm,
        interval_hours=ih,
        auto_run=bool(auto_run),
    )
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}", status_code=303)
