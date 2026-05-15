"""Cluster web routes: list, detail, create form, polled status fragment, chart fragments."""

from __future__ import annotations

import json

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from greenhouse_server.deps import ClusterServiceDep, PlantDbDep, RepoDep
from greenhouse_server.services.charts import (
    ALLOWED_HOURS,
    build_cluster_chart_payload,
    build_heatmap_payload,
    build_overlay_payload,
)
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

_EMPTY_RATIONALE: list[dict] = []

router = APIRouter(include_in_schema=False)

CLUSTER_METRICS = ("soil_moisture", "temperature", "env_humidity", "light")


@router.get("/clusters")
def list_clusters(request: Request, repo: RepoDep):
    clusters = repo.list_clusters()
    return templates.TemplateResponse(request, "clusters/list.html", base_context(request, clusters=clusters))


@router.get("/clusters/new")
def new_cluster_form(request: Request):
    return templates.TemplateResponse(request, "clusters/new.html", base_context(request))


@router.post("/clusters")
def create_cluster(
    request: Request,
    repo: RepoDep,
    name: str = Form(...),
    location: str = Form(""),
    environment: str = Form("indoor"),
):
    cluster_id = repo.add_cluster(name=name, location=location or None, environment=environment)
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}", status_code=303)


@router.get("/clusters/{cluster_id}/edit")
def edit_cluster_form(request: Request, cluster_id: int, repo: RepoDep):
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(404, "Cluster not found")
    return templates.TemplateResponse(request, "clusters/edit.html", base_context(request, cluster=cluster))


@router.post("/clusters/{cluster_id}/edit")
def update_cluster(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    name: str = Form(...),
    location: str = Form(""),
    environment: str = Form("indoor"),
):
    updated = repo.update_cluster(
        cluster_id,
        name=name,
        location=location or None,
        environment=environment,
    )
    if not updated:
        raise HTTPException(404, "Cluster not found")
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}", status_code=303)


@router.delete("/clusters/{cluster_id}", response_class=HTMLResponse)
def delete_cluster(cluster_id: int, repo: RepoDep):
    """HTMX-targeted delete; returns an empty HTML body so the row is removed."""
    deleted = repo.delete_cluster(cluster_id)
    if not deleted:
        raise HTTPException(404, "Cluster not found")
    repo.session.commit()
    return HTMLResponse("")


@router.get("/clusters/{cluster_id}")
def cluster_detail(
    request: Request,
    cluster_id: int,
    svc: ClusterServiceDep,
    repo: RepoDep,
    plant_db: PlantDbDep,
    hours: int = Query(24, ge=1, le=8760),
):
    status = svc.get_cluster_status(cluster_id)
    if status is None:
        raise HTTPException(404, "Cluster not found")

    # Pre-build chart payloads so charts render on first page load
    chart_payloads = {
        metric: build_cluster_chart_payload(repo, plant_db, cluster_id, hours, metric)  # type: ignore[arg-type]
        for metric in CLUSTER_METRICS
    }
    chart_payloads_json = {metric: json.dumps(payload) for metric, payload in chart_payloads.items()}
    # Threshold per metric is reused by stat tiles to render the range indicator.
    chart_thresholds = {metric: payload.get("threshold", {}) for metric, payload in chart_payloads.items()}

    # Decision rationale: latest persisted DecisionLog with decoded reasons[]
    rationale_reasons: list[dict] = _EMPTY_RATIONALE
    logs = repo.list_decision_logs(cluster_id, limit=1)
    if logs:
        log = logs[0]
        try:
            payload = json.loads(log.payload_json)
            rationale_reasons = payload.get("reasons", [])
        except (json.JSONDecodeError, TypeError):
            rationale_reasons = _EMPTY_RATIONALE

    return templates.TemplateResponse(
        request,
        "clusters/detail.html",
        base_context(
            request,
            status=status,
            cluster_id=cluster_id,
            hours=hours,
            allowed_hours=sorted(ALLOWED_HOURS),
            metrics=CLUSTER_METRICS,
            chart_payloads=chart_payloads_json,
            chart_thresholds=chart_thresholds,
            rationale_reasons=rationale_reasons,
        ),
    )


@router.get("/clusters/{cluster_id}/status-fragment")
def cluster_status_fragment(request: Request, cluster_id: int, svc: ClusterServiceDep):
    status = svc.get_cluster_status(cluster_id)
    if status is None:
        raise HTTPException(404, "Cluster not found")
    return templates.TemplateResponse(
        request, "partials/_cluster_status.html", base_context(request, status=status, cluster_id=cluster_id)
    )


@router.get("/clusters/{cluster_id}/chart-fragment")
def cluster_chart_fragment(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    plant_db: PlantDbDep,
    metric: str = Query("soil_moisture"),
    hours: int = Query(24, ge=1, le=8760),
):
    if metric not in CLUSTER_METRICS:
        raise HTTPException(400, f"Unsupported metric: {metric}")
    payload = build_cluster_chart_payload(repo, plant_db, cluster_id, hours, metric)  # type: ignore[arg-type]
    if not payload:
        raise HTTPException(404, "Cluster not found")
    return templates.TemplateResponse(
        request,
        "partials/_chart_panel.html",
        base_context(request, metric=metric, hours=hours, payload_json=json.dumps(payload)),
    )


@router.get("/clusters/{cluster_id}/overlay-fragment")
def cluster_overlay_fragment(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    hours: int = Query(72, ge=1, le=8760),
):
    payload = build_overlay_payload(repo, cluster_id, hours)
    if payload is None:
        raise HTTPException(404, "Cluster not found")
    return templates.TemplateResponse(
        request,
        "partials/_chart_overlay.html",
        base_context(request, cluster_id=cluster_id, hours=hours, payload_json=payload.model_dump_json()),
    )


@router.get("/clusters/{cluster_id}/heatmap-fragment")
def cluster_heatmap_fragment(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    days: int = Query(30, ge=1, le=365),
):
    payload = build_heatmap_payload(repo, cluster_id, days)
    if payload is None:
        raise HTTPException(404, "Cluster not found")
    return templates.TemplateResponse(
        request,
        "partials/_heatmap_panel.html",
        base_context(request, cluster_id=cluster_id, days=days, payload=payload),
    )
