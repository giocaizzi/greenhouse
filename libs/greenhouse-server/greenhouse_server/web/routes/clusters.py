"""Cluster web routes: list, detail, create form, polled status fragment, chart fragments."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from greenhouse_core.logic.timing import active_quiet_window
from greenhouse_server.deps import (
    MAX_LOOKBACK_HOURS,
    ClusterServiceDep,
    PlantDbDep,
    RepoDep,
    not_found_as_404,
    require_cluster,
    require_metric,
)
from greenhouse_server.services.charts import (
    ALLOWED_HOURS,
    build_cluster_chart_payload,
    build_heatmap_payload,
    build_overlay_payload,
)
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates
from greenhouse_server.web.weekdays import WEEKDAY_BITS, WEEKDAY_LABELS, format_weekday_mask

if TYPE_CHECKING:
    from greenhouse_core.plant_db import PlantDatabase
    from greenhouse_core.repository import EffectiveConfig, IrrigationRepository
    from greenhouse_server.services.charts import Metric

_EMPTY_RATIONALE: list[dict[str, Any]] = []

router = APIRouter(include_in_schema=False)

CLUSTER_METRICS: tuple[Metric, ...] = ("soil_moisture", "temperature", "env_humidity", "light")


@router.get("/clusters")
def list_clusters(request: Request, repo: RepoDep) -> Response:
    """Render the cluster list page."""
    clusters = repo.list_clusters()
    return templates.TemplateResponse(request, "clusters/list.html", base_context(request, clusters=clusters))


@router.get("/clusters/new")
def new_cluster_form(request: Request) -> Response:
    """Render the new-cluster form."""
    return templates.TemplateResponse(request, "clusters/new.html", base_context(request))


@router.post("/clusters")
def create_cluster(
    request: Request,
    repo: RepoDep,
    name: str = Form(...),
    location: str = Form(""),
    environment: str = Form("indoor"),
) -> Response:
    """Create a cluster from the form and redirect to its detail page."""
    cluster_id = repo.add_cluster(name=name, location=location or None, environment=environment)
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}", status_code=303)


@router.get("/clusters/{cluster_id}/edit")
def edit_cluster_form(request: Request, cluster_id: int, repo: RepoDep) -> Response:
    """Render the edit form of an existing cluster."""
    cluster = require_cluster(repo, cluster_id)
    return templates.TemplateResponse(request, "clusters/edit.html", base_context(request, cluster=cluster))


@router.post("/clusters/{cluster_id}/edit")
def update_cluster(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    name: str = Form(...),
    location: str = Form(""),
    environment: str = Form("indoor"),
) -> Response:
    """Save the cluster form and redirect to the detail page."""
    require_cluster(repo, cluster_id)
    repo.update_cluster(
        cluster_id,
        name=name,
        location=location or None,
        environment=environment,
    )
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}", status_code=303)


@router.delete("/clusters/{cluster_id}", response_class=HTMLResponse)
def delete_cluster(cluster_id: int, repo: RepoDep) -> Response:
    """HTMX-targeted delete; returns an empty HTML body so the row is removed."""
    require_cluster(repo, cluster_id)
    repo.delete_cluster(cluster_id)
    repo.commit()
    return HTMLResponse("")


def _plants_by_id(repo: IrrigationRepository, cluster_id: int) -> dict[int, object]:
    return {p.id: p for p in repo.get_plants_in_cluster(cluster_id)}


def _cluster_chart_payloads(
    repo: IrrigationRepository, plant_db: PlantDatabase, cluster_id: int, hours: int
) -> tuple[dict[Metric, str], dict[Metric, Any]]:
    """Pre-build every metric's chart payload (as JSON) so charts render on first page load.

    Also returns each metric's threshold, which the stat tiles reuse for the range indicator.
    """
    chart_payloads = {
        metric: build_cluster_chart_payload(repo, plant_db, cluster_id, hours, metric) for metric in CLUSTER_METRICS
    }
    chart_payloads_json = {metric: json.dumps(payload) for metric, payload in chart_payloads.items()}
    chart_thresholds = {metric: payload.get("threshold", {}) for metric, payload in chart_payloads.items()}
    return chart_payloads_json, chart_thresholds


def _rationale_reasons(repo: IrrigationRepository, cluster_id: int) -> list[dict[str, Any]]:
    """Decoded ``reasons[]`` of the latest persisted DecisionLog (the shared empty list when none decode)."""
    rationale_reasons: list[dict[str, Any]] = _EMPTY_RATIONALE
    logs = repo.list_decision_logs(cluster_id, limit=1)
    if logs:
        log = logs[0]
        try:
            payload = json.loads(log.payload_json)
            rationale_reasons = payload.get("reasons", [])
        except (json.JSONDecodeError, TypeError):
            rationale_reasons = _EMPTY_RATIONALE
    return rationale_reasons


def _window_rows(repo: IrrigationRepository, cluster_id: int) -> list[dict[str, Any]]:
    """The cluster's irrigation windows as template rows, each with its weekday label."""
    return [
        {
            "id": w.id,
            "start_hour": w.start_hour,
            "end_hour": w.end_hour,
            "weekday_mask": w.weekday_mask,
            "weekday_label": format_weekday_mask(w.weekday_mask),
            "label": w.label,
        }
        for w in repo.list_irrigation_windows(cluster_id)
    ]


def _detail_data(repo: IrrigationRepository, plant_db: PlantDatabase, cluster_id: int, hours: int) -> dict[str, Any]:
    """Detail-page template data besides the live status and the quiet-hours flag.

    Chart controls and pre-built payloads, the decision rationale, the inline config, the
    irrigation windows (with the weekday vocabulary of their form) and the sensor→plant map.
    """
    chart_payloads_json, chart_thresholds = _cluster_chart_payloads(repo, plant_db, cluster_id, hours)
    return {
        "allowed_hours": sorted(ALLOWED_HOURS),
        "metrics": CLUSTER_METRICS,
        "chart_payloads": chart_payloads_json,
        "chart_thresholds": chart_thresholds,
        "rationale_reasons": _rationale_reasons(repo, cluster_id),
        # Inline-config section data: the declared row (nullable per-field
        # overrides) plus the effective resolved view used by the engine. Both
        # shapes feed ``partials/_config_field.html`` so it can render the
        # current value next to its source badge.
        "declared_config": repo.get_irrigation_config(cluster_id),
        "effective_config": repo.get_effective_config(cluster_id),
        "windows": _window_rows(repo, cluster_id),
        "weekday_bits": WEEKDAY_BITS,
        "weekday_labels": WEEKDAY_LABELS,
        # Sensor → plant lookup so the inline #sensors table can render the
        # plant↔sensor relationship with the ``↳`` glyph without extra queries.
        "plants_by_id": _plants_by_id(repo, cluster_id),
    }


@router.get("/clusters/{cluster_id}")
def cluster_detail(
    request: Request,
    cluster_id: int,
    svc: ClusterServiceDep,
    repo: RepoDep,
    plant_db: PlantDbDep,
    hours: int = Query(24, ge=1, le=MAX_LOOKBACK_HOURS),
) -> Response:
    """Render the unified cluster detail page (status, charts, config, windows, devices)."""
    with not_found_as_404("Cluster not found"):
        status = svc.get_cluster_status(cluster_id)

    data = _detail_data(repo, plant_db, cluster_id, hours)
    effective_config: EffectiveConfig = data["effective_config"]

    # "Are we in quiet hours right now?" — drives the hx-confirm guard on
    # the manual irrigate button. Same helper and effective resolution as the
    # engine's quiet-hours gate, so the UI never disagrees with the engine.
    prefs = repo.get_preferences()
    quiet_window = active_quiet_window(
        effective_config, now_unix=int(time.time()), tz_name=prefs.timezone if prefs else None
    )

    return templates.TemplateResponse(
        request,
        "clusters/detail.html",
        base_context(
            request,
            status=status,
            cluster_id=cluster_id,
            hours=hours,
            quiet_active_now=quiet_window is not None,
            **data,
        ),
    )


@router.get("/clusters/{cluster_id}/status-fragment")
def cluster_status_fragment(request: Request, cluster_id: int, svc: ClusterServiceDep) -> Response:
    """Live status fragment used by the cluster detail page's "Live status" panel.

    Retained alongside the richer ``card-fragment`` endpoint so existing
    bookmarks and the cluster detail page keep working unchanged.
    """
    with not_found_as_404("Cluster not found"):
        status = svc.get_cluster_status(cluster_id)
    return templates.TemplateResponse(
        request, "partials/_cluster_status.html", base_context(request, status=status, cluster_id=cluster_id)
    )


@router.get("/clusters/{cluster_id}/card-fragment")
def cluster_card_fragment(request: Request, cluster_id: int, svc: ClusterServiceDep, repo: RepoDep) -> Response:
    """Rich cluster card body used by the cluster-centric home view.

    Returns ``partials/_cluster_card.html`` with plants/sensors/irrigators
    rolled up inline so the relationship between them is visible without
    drilling into the cluster detail page.
    """
    with not_found_as_404("Cluster not found"):
        status = svc.get_cluster_status(cluster_id)
    return templates.TemplateResponse(
        request,
        "partials/_cluster_card.html",
        base_context(request, status=status, plants_by_id=_plants_by_id(repo, cluster_id)),
    )


@router.get("/clusters/{cluster_id}/chart-fragment")
def cluster_chart_fragment(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    plant_db: PlantDbDep,
    metric: str = Query("soil_moisture"),
    hours: int = Query(24, ge=1, le=MAX_LOOKBACK_HOURS),
) -> Response:
    """Render one metric's cluster chart panel (HTMX fragment)."""
    with not_found_as_404("Cluster not found"):
        payload = build_cluster_chart_payload(repo, plant_db, cluster_id, hours, require_metric(metric))
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
    hours: int = Query(72, ge=1, le=MAX_LOOKBACK_HOURS),
) -> Response:
    """Render the multi-metric overlay chart panel (HTMX fragment)."""
    with not_found_as_404("Cluster not found"):
        payload = build_overlay_payload(repo, cluster_id, hours)
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
) -> Response:
    """Render the irrigation weekday-by-hour heatmap panel (HTMX fragment)."""
    with not_found_as_404("Cluster not found"):
        payload = build_heatmap_payload(repo, cluster_id, days)
    return templates.TemplateResponse(
        request,
        "partials/_heatmap_panel.html",
        base_context(request, cluster_id=cluster_id, days=days, payload=payload),
    )
