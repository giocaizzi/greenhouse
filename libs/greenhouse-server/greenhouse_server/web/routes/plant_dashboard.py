"""Per-plant dashboard web route + chart fragment."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from greenhouse_core.models import Plant
from greenhouse_core.repository import SameClusterMoveError
from greenhouse_server.deps import (
    MAX_LOOKBACK_HOURS,
    PlantDbDep,
    PlantHealthServiceDep,
    RepoDep,
    require_metric,
    require_plant_in_cluster,
)
from greenhouse_server.services.charts import (
    ALLOWED_HOURS,
    build_plant_chart_payload,
    build_plant_health_timeline_payload,
)
from greenhouse_server.services.maintenance import collect_learning_alerts
from greenhouse_server.web.context import base_context
from greenhouse_server.web.filters import relative_age
from greenhouse_server.web.templating import templates

if TYPE_CHECKING:
    from greenhouse_core.models import Irrigator, Sensor, SensorReading
    from greenhouse_core.plant_db import PlantDatabase
    from greenhouse_core.repository import IrrigationRepository
    from greenhouse_server.services.charts import Metric

router = APIRouter(include_in_schema=False)

METRICS: tuple[Metric, ...] = ("soil_moisture", "temperature", "env_humidity", "light")


@router.get("/clusters/{cluster_id}/plants/{plant_id}")
def plant_dashboard(
    request: Request,
    cluster_id: int,
    plant_id: int,
    repo: RepoDep,
    plant_db: PlantDbDep,
    health_svc: PlantHealthServiceDep,
    hours: int = Query(24, ge=1, le=MAX_LOOKBACK_HOURS),
):
    plant = require_plant_in_cluster(repo, cluster_id, plant_id)
    cluster = repo.get_cluster(cluster_id)
    other_clusters = [c for c in repo.list_clusters() if c.id != cluster_id]
    plant_sensors = [s for s in repo.get_sensors_in_cluster(cluster_id) if s.plant_id == plant_id]
    latest_readings = _latest_readings(repo, plant_sensors)
    # Plant care info (best-effort species lookup)
    care_info = plant_db.lookup_species(plant.species)
    cluster_irrigator = repo.get_irrigator_for_cluster(cluster_id)
    recent_events = _recent_events(repo, cluster_irrigator, hours)
    plant_alerts = _plant_alerts(repo, plant_db, cluster_id, plant)
    chart_payloads_json = _chart_payloads_json(repo, plant_db, plant_id, hours)
    # Health score + 90-day history for the hero card
    health_score: float | None = health_svc.compute_score(plant_id)["score"]
    health_history = repo.list_plant_health_history(plant_id, days=90)
    last_irrigated_relative: str = relative_age(
        _last_irrigated_ts(repo, cluster_irrigator), missing="never", stale_after=None
    )

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


def _chart_payloads_json(
    repo: IrrigationRepository, plant_db: PlantDatabase, plant_id: int, hours: int
) -> dict[Metric, str]:
    """Every metric's chart payload as JSON, pre-built so the page renders with data on first load."""
    chart_payloads = {metric: build_plant_chart_payload(repo, plant_db, plant_id, hours, metric) for metric in METRICS}
    return {metric: json.dumps(payload) for metric, payload in chart_payloads.items()}


def _latest_readings(repo: IrrigationRepository, plant_sensors: list[Sensor]) -> dict[int, SensorReading | None]:
    """Latest reading (last 24 h) per linked sensor, keyed by sensor id."""
    latest_readings = {}
    for s in plant_sensors:
        recent = repo.get_recent_readings(s.id, hours=24)
        latest_readings[s.id] = recent[0] if recent else None
    return latest_readings


def _recent_events(repo: IrrigationRepository, cluster_irrigator: Irrigator | None, hours: int) -> list[Any]:
    """Newest ten irrigation events of the cluster's irrigator (a plant inherits its cluster's events)."""
    recent_events: list = []
    if cluster_irrigator is not None:
        recent_events.extend(repo.get_recent_events(cluster_irrigator.id, hours=hours))
    recent_events.sort(key=lambda e: e.timestamp, reverse=True)
    recent_events = recent_events[:10]
    return recent_events


def _plant_alerts(
    repo: IrrigationRepository, plant_db: PlantDatabase, cluster_id: int, plant: Plant
) -> list[dict[str, Any]]:
    """The cluster's learning alerts whose message mentions this plant's species."""
    all_alerts = collect_learning_alerts(repo, cluster_id, plant_db)
    return [a for a in all_alerts if plant.species.lower() in (a.get("message") or "").lower()]


def _last_irrigated_ts(repo: IrrigationRepository, cluster_irrigator: Irrigator | None) -> int | None:
    """Timestamp of the newest event (last 90 days) on the cluster's irrigator, or ``None``."""
    last_irrigated_ts: int | None = None
    if cluster_irrigator is not None:
        events = repo.get_recent_events(cluster_irrigator.id, hours=90 * 24)
        for ev in events:
            if last_irrigated_ts is None or ev.timestamp > last_irrigated_ts:
                last_irrigated_ts = ev.timestamp
    return last_irrigated_ts


@router.get("/clusters/{cluster_id}/plants/{plant_id}/chart-fragment")
def plant_chart_fragment(
    request: Request,
    cluster_id: int,
    plant_id: int,
    repo: RepoDep,
    plant_db: PlantDbDep,
    metric: str = Query("soil_moisture"),
    hours: int = Query(24, ge=1, le=MAX_LOOKBACK_HOURS),
):
    chart_metric = require_metric(metric)
    require_plant_in_cluster(repo, cluster_id, plant_id)
    payload = build_plant_chart_payload(repo, plant_db, plant_id, hours, chart_metric)
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
    plant = require_plant_in_cluster(repo, cluster_id, plant_id)
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
    require_plant_in_cluster(repo, cluster_id, plant_id)
    if not repo.get_cluster(target_cluster_id):
        raise HTTPException(404, "Target cluster not found")
    try:
        repo.move_plant(plant_id, target_cluster_id)
    except SameClusterMoveError as exc:
        raise HTTPException(400, str(exc)) from exc
    repo.commit()
    return RedirectResponse(url=f"/clusters/{target_cluster_id}/plants/{plant_id}", status_code=303)
