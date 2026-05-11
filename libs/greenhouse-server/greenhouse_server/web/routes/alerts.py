"""Alert inbox web routes — list, badge, ack/resolve fragments."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from greenhouse_server.deps import PlantDbDep, RepoDep
from greenhouse_server.services.alerts import sync_all_alerts
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/alerts")
def alert_list(
    request: Request,
    repo: RepoDep,
    status: str | None = Query(None),
    cluster_id: int | None = Query(None),
    plant_id: int | None = Query(None),
):
    alerts = repo.list_alerts(status=status, cluster_id=cluster_id, plant_id=plant_id, limit=200)
    open_count = repo.count_open_alerts()
    return templates.TemplateResponse(
        request,
        "alerts/list.html",
        base_context(
            request,
            alerts=alerts,
            open_count=open_count,
            current_status=status,
            current_cluster_id=cluster_id,
            current_plant_id=plant_id,
        ),
    )


@router.post("/alerts/{alert_id}/ack")
def ack_alert(request: Request, alert_id: int, repo: RepoDep):
    alert = repo.acknowledge_alert(alert_id)
    repo.session.commit()
    toast = json.dumps({"severity": "success", "title": "Acknowledged", "message": "Alert moved to triage."})
    return templates.TemplateResponse(
        request,
        "partials/_alert_row.html",
        base_context(request, alert=alert),
        headers={"HX-Toast": toast},
    )


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(request: Request, alert_id: int, repo: RepoDep):
    alert = repo.resolve_alert(alert_id)
    repo.session.commit()
    toast = json.dumps({"severity": "success", "title": "Resolved", "message": "Alert marked as resolved."})
    return templates.TemplateResponse(
        request,
        "partials/_alert_row.html",
        base_context(request, alert=alert),
        headers={"HX-Toast": toast},
    )


@router.post("/alerts/sync")
def sync_alerts(request: Request, repo: RepoDep, plant_db: PlantDbDep):
    open_count = sync_all_alerts(repo, plant_db)
    repo.session.commit()
    alerts = repo.list_alerts(limit=200)
    toast = json.dumps({"severity": "info", "title": "Synced", "message": f"{open_count} open alert(s) after sync."})
    return templates.TemplateResponse(
        request,
        "partials/_alert_list_body.html",
        base_context(request, alerts=alerts),
        headers={"HX-Toast": toast},
    )


@router.get("/alerts/badge")
def alert_badge(request: Request, repo: RepoDep):
    count = repo.count_open_alerts()
    if count == 0:
        return HTMLResponse("")
    return HTMLResponse(f'<span class="bell__count">{count}</span>')
