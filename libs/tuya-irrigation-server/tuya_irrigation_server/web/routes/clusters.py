"""Cluster web routes: list, detail, create form, polled status fragment, chart fragments."""

from __future__ import annotations

import json

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from tuya_irrigation_server.deps import ClusterServiceDep, PlantDbDep, RepoDep
from tuya_irrigation_server.services.charts import ALLOWED_HOURS, build_cluster_chart_payload
from tuya_irrigation_server.web.context import base_context
from tuya_irrigation_server.web.templating import templates

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
