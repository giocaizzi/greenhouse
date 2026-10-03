"""Action web routes: irrigate, monitor, check, sync, plants-sync."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request

from greenhouse_server.deps import (
    ClusterServiceDep,
    IrrigationServiceDep,
    RepoDep,
    SyncServiceDep,
    require_cluster,
)
from greenhouse_server.services.cluster import ClusterNotFoundError, PlantNotFoundError
from greenhouse_server.services.irrigation import check_has_alerts
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.post("/clusters/{cluster_id}/irrigate")
def irrigate(
    request: Request,
    cluster_id: int,
    svc: IrrigationServiceDep,
    repo: RepoDep,
    dry_run: str = Form(""),
    no_sync: str = Form(""),
    temp_override: str = Form(""),
    force: str = Form(""),
):
    """Run the irrigation pipeline from the inline action bar on the cluster
    detail page.

    ``force`` is set to ``"true"`` when the user clicks Irrigate during quiet
    hours and confirms the hx-confirm prompt. It plumbs through to the
    engine as ``bypass_quiet_hours``; the decision still logs a warning
    Reason so the override is in the audit trail.
    """
    temp = float(temp_override) if temp_override.strip() else None
    forced = force.strip().lower() in ("true", "on", "1")
    result = svc.run_irrigation_pipeline(
        cluster_id=cluster_id,
        temp_override=temp,
        dry_run=bool(dry_run),
        no_sync=bool(no_sync),
        force=forced,
    )
    repo.commit()
    return templates.TemplateResponse(
        request, "partials/_decision_panel.html", base_context(request, result=result, cluster_id=cluster_id)
    )


@router.get("/clusters/{cluster_id}/monitor")
def monitor(request: Request, cluster_id: int, repo: RepoDep, svc: IrrigationServiceDep):
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
):
    require_cluster(repo, cluster_id)
    result = svc.check_cluster(cluster_id)
    repo.commit()
    return templates.TemplateResponse(
        request,
        "partials/_check_result.html",
        base_context(request, results=[result], has_alerts=check_has_alerts([result])),
    )


@router.post("/check")
def check_all(request: Request, svc: IrrigationServiceDep, repo: RepoDep):
    results = svc.check_all_clusters()
    repo.commit()
    has_alerts = check_has_alerts(results)
    return templates.TemplateResponse(
        request, "partials/_check_result.html", base_context(request, results=results, has_alerts=has_alerts)
    )


@router.post("/sync")
def sync_all(request: Request, svc: SyncServiceDep, repo: RepoDep, hours: str = Form("24")):
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
    plant_id: str = Form(""),
    cluster_id: str = Form(""),
):
    pid = int(plant_id) if plant_id.strip() else None
    cid = int(cluster_id) if cluster_id.strip() else None
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
