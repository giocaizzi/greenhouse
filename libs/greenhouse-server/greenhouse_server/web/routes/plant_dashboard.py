"""Per-plant dashboard web route + chart fragment."""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from greenhouse_core.models import Plant
from greenhouse_core.repository import SameClusterMoveError
from greenhouse_server.deps import PlantDbDep, PlantHealthServiceDep, RepoDep
from greenhouse_server.services.charts import (
    ALLOWED_HOURS,
    build_plant_chart_payload,
    build_plant_health_timeline_payload,
)
from greenhouse_server.services.maintenance import collect_learning_alerts
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)

METRICS = ("soil_moisture", "temperature", "env_humidity", "light")


def _get_plant_or_404(repo, plant_id: int, cluster_id: int) -> Plant:
    plant: Plant | None = repo.session.get(Plant, plant_id)
    if plant is None or plant.cluster_id != cluster_id:
        raise HTTPException(404, "Plant not found")
    return plant


@router.get("/clusters/{cluster_id}/plants/{plant_id}")
def plant_dashboard(
    request: Request,
    cluster_id: int,
    plant_id: int,
    repo: RepoDep,
    plant_db: PlantDbDep,
    health_svc: PlantHealthServiceDep,
    hours: int = Query(24, ge=1, le=8760),
):
    plant = _get_plant_or_404(repo, plant_id, cluster_id)
    cluster = repo.get_cluster(cluster_id)
    other_clusters = [c for c in repo.list_clusters() if c.id != cluster_id]

    sensors_all = repo.get_sensors_in_cluster(cluster_id)
    plant_sensors = [s for s in sensors_all if s.plant_id == plant_id]

    # Latest reading per linked sensor
    latest_readings = {}
    for s in plant_sensors:
        recent = repo.get_recent_readings(s.id, hours=24)
        latest_readings[s.id] = recent[0] if recent else None

    # Plant care info (best-effort species lookup)
    care_info = plant_db.lookup_species(plant.species)

    # Recent irrigation events across the cluster (plant inherits cluster events)
    recent_events: list = []
    for irr in repo.get_irrigators_in_cluster(cluster_id):
        recent_events.extend(repo.get_recent_events(irr.id, hours=hours))
    recent_events.sort(key=lambda e: e.timestamp, reverse=True)
    recent_events = recent_events[:10]

    # Learning alerts for the cluster, filtered to those mentioning this plant species
    all_alerts = collect_learning_alerts(repo, cluster_id, plant_db)
    plant_alerts = [a for a in all_alerts if plant.species.lower() in (a.get("message") or "").lower()]

    # Pre-build chart payloads so the page renders with data on first load
    chart_payloads = {metric: build_plant_chart_payload(repo, plant_db, plant_id, hours, metric) for metric in METRICS}
    chart_payloads_json = {metric: json.dumps(payload) for metric, payload in chart_payloads.items()}

    # Health score + 90-day history for the hero card
    health_result = health_svc.compute_score(plant_id)
    health_score: float | None = health_result["score"]
    health_history = repo.list_plant_health_history(plant_id, days=90)

    # Last-irrigated relative timestamp (newest event across all irrigators)
    last_irrigated_ts: int | None = None
    for irr in repo.get_irrigators_in_cluster(cluster_id):
        events = repo.get_recent_events(irr.id, hours=90 * 24)
        for ev in events:
            if last_irrigated_ts is None or ev.timestamp > last_irrigated_ts:
                last_irrigated_ts = ev.timestamp
    last_irrigated_relative: str = _relative_time(last_irrigated_ts)

    return templates.TemplateResponse(
        request,
        "plants/dashboard.html",
        base_context(
            request,
            cluster=cluster,
            other_clusters=other_clusters,
            plant=plant,
            plant_sensors=plant_sensors,
            latest_readings=latest_readings,
            care_info=care_info,
            recent_events=recent_events,
            alerts=plant_alerts,
            hours=hours,
            allowed_hours=sorted(ALLOWED_HOURS),
            metrics=METRICS,
            chart_payloads=chart_payloads_json,
            health_score=health_score,
            health_history=health_history,
            last_irrigated_relative=last_irrigated_relative,
        ),
    )


def _relative_time(ts: int | None) -> str:
    """Return a human-readable relative time string for a Unix timestamp."""
    if ts is None:
        return "never"
    delta = max(0, int(time.time() - ts))
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


@router.get("/clusters/{cluster_id}/plants/{plant_id}/chart-fragment")
def plant_chart_fragment(
    request: Request,
    cluster_id: int,
    plant_id: int,
    repo: RepoDep,
    plant_db: PlantDbDep,
    metric: str = Query("soil_moisture"),
    hours: int = Query(24, ge=1, le=8760),
):
    if metric not in METRICS:
        raise HTTPException(400, f"Unsupported metric: {metric}")
    _get_plant_or_404(repo, plant_id, cluster_id)
    payload = build_plant_chart_payload(repo, plant_db, plant_id, hours, metric)
    if not payload:
        raise HTTPException(404, "Plant not found")
    return templates.TemplateResponse(
        request,
        "partials/_chart_panel.html",
        base_context(request, metric=metric, hours=hours, payload_json=json.dumps(payload)),
    )


@router.get("/clusters/{cluster_id}/plants/{plant_id}/health-fragment")
def plant_health_fragment(
    request: Request,
    cluster_id: int,
    plant_id: int,
    repo: RepoDep,
):
    plant = _get_plant_or_404(repo, plant_id, cluster_id)
    payload = build_plant_health_timeline_payload(repo, plant_id)
    if payload is None:
        raise HTTPException(404, "Plant not found")
    return templates.TemplateResponse(
        request,
        "partials/_plant_health_chart.html",
        base_context(request, plant=plant, payload_json=payload.model_dump_json()),
    )


@router.post("/clusters/{cluster_id}/plants/{plant_id}/move")
def move_plant_web(
    request: Request,
    cluster_id: int,
    plant_id: int,
    repo: RepoDep,
    target_cluster_id: int = Form(...),
):
    """Move a plant to a different cluster (server-rendered form submit)."""
    _get_plant_or_404(repo, plant_id, cluster_id)
    if not repo.get_cluster(target_cluster_id):
        raise HTTPException(404, "Target cluster not found")
    try:
        repo.move_plant(plant_id, target_cluster_id)
    except SameClusterMoveError as exc:
        raise HTTPException(400, str(exc)) from exc
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{target_cluster_id}/plants/{plant_id}", status_code=303)
