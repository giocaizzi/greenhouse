"""Action web routes: irrigate, monitor, check, sync, plants-sync."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import Response

from greenhouse_server.deps import (
    ClusterServiceDep,
    IrrigationServiceDep,
    RepoDep,
    SyncServiceDep,
    require_cluster,
)
from greenhouse_server.services.errors import ClusterNotFoundError, PlantNotFoundError
from greenhouse_server.services.irrigation import check_has_alerts
from greenhouse_server.web.context import base_context
from greenhouse_server.web.forms import parsed_or_400
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@dataclass(frozen=True, slots=True)
class _IrrigateForm:
    """``POST /clusters/{cluster_id}/irrigate``: the action bar's flags (a ticked box posts a value)."""

    cluster_id: Annotated[int, Path()]
    dry_run: Annotated[str, Form()] = ""
    no_sync: Annotated[str, Form()] = ""
    temp_override: Annotated[str, Form()] = ""
    force: Annotated[str, Form()] = ""


@router.post("/clusters/{cluster_id}/irrigate")
def irrigate(
    request: Request, svc: IrrigationServiceDep, repo: RepoDep, form: Annotated[_IrrigateForm, Depends()]
) -> Response:
    """Run the irrigation pipeline from the cluster detail page's action bar (HTMX fragment).

    ``force`` is set to ``"true"`` when the user clicks Irrigate during quiet
    hours and confirms the hx-confirm prompt. It plumbs through to the
    engine as ``bypass_quiet_hours``; the decision still logs a warning
    Reason so the override is in the audit trail.
    """
    # Same as POST /api/v1/clusters/{id}/irrigate: an unknown cluster is a 404 page, not the
    # pipeline's error dict rendered into the decision panel.
    require_cluster(repo, form.cluster_id)
    temp = parsed_or_400(form.temp_override, float, error="Invalid temp_override: must be a number")
    forced = form.force.strip().lower() in ("true", "on", "1")
    result = svc.run_irrigation_pipeline(
        cluster_id=form.cluster_id,
        temp_override=temp,
        dry_run=bool(form.dry_run),
        no_sync=bool(form.no_sync),
        force=forced,
    )
    repo.commit()
    return templates.TemplateResponse(
        request, "partials/_decision_panel.html", base_context(request, result=result, cluster_id=form.cluster_id)
    )


@router.get("/clusters/{cluster_id}/monitor")
def monitor(request: Request, cluster_id: int, repo: RepoDep, svc: IrrigationServiceDep) -> Response:
    """Render the per-sensor soil-moisture status of a cluster (HTMX fragment)."""
    # Same path as GET /api/v1/clusters/{id}/monitor: 404 for an unknown cluster, refresh stale sensors, keep the rows.
    require_cluster(repo, cluster_id)
    result = svc.monitor_cluster(cluster_id=cluster_id)
    repo.commit()
    return templates.TemplateResponse(
        request, "partials/_monitor_panel.html", base_context(request, result=result, cluster_id=cluster_id)
    )


@router.post("/clusters/{cluster_id}/check")
def check_single(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    svc: IrrigationServiceDep,
) -> Response:
    """Run the check for one cluster and render the result banner (HTMX fragment)."""
    require_cluster(repo, cluster_id)
    result = svc.check_cluster(cluster_id)
    repo.commit()
    return templates.TemplateResponse(
        request,
        "partials/_check_result.html",
        base_context(request, results=[result], has_alerts=check_has_alerts([result])),
    )


@router.post("/check")
def check_all(request: Request, svc: IrrigationServiceDep, repo: RepoDep) -> Response:
    """Run the check across every cluster and render the result banner (HTMX fragment)."""
    results = svc.check_all_clusters()
    repo.commit()
    has_alerts = check_has_alerts(results)
    return templates.TemplateResponse(
        request, "partials/_check_result.html", base_context(request, results=results, has_alerts=has_alerts)
    )


@router.post("/sync")
def sync_all(request: Request, svc: SyncServiceDep, repo: RepoDep, hours: Annotated[str, Form()] = "24") -> Response:
    """Sync every sensor from the Tuya Cloud and render the sync summary (HTMX fragment)."""
    try:
        hrs = int(hours)
    except ValueError as exc:
        raise HTTPException(400, "Invalid hours") from exc
    result = svc.sync_all_sensors(hours=hrs)
    repo.commit()
    return templates.TemplateResponse(request, "partials/_sync_result.html", base_context(request, result=result))


@router.post("/plants/sync")
def sync_plants(
    request: Request,
    repo: RepoDep,
    svc: ClusterServiceDep,
    plant_id: Annotated[str, Form()] = "",
    cluster_id: Annotated[str, Form()] = "",
) -> Response:
    """Refresh plant care data from the plant database and render the summary (HTMX fragment)."""
    pid = parsed_or_400(plant_id, int, error="Invalid plant_id")
    cid = parsed_or_400(cluster_id, int, error="Invalid cluster_id")
    try:
        synced, errors = svc.sync_plants(plant_id=pid, cluster_id=cid)
    except PlantNotFoundError:
        raise HTTPException(404, f"Plant {pid} not found") from None
    except ClusterNotFoundError:
        raise HTTPException(404, "Cluster not found") from None

    repo.commit()
    return templates.TemplateResponse(
        request,
        "partials/_sync_result.html",
        base_context(request, result={"synced": synced, "errors": errors, "kind": "plants"}),
    )
