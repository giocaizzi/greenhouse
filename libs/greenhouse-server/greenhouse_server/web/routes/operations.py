"""Action web routes: irrigate, monitor, check, sync, plants-sync."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request

from greenhouse_server.deps import (
    ClusterServiceDep,
    IrrigationServiceDep,
    RepoDep,
    SessionDep,
    SyncServiceDep,
    require_cluster,
)
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.post("/clusters/{cluster_id}/irrigate")
def irrigate(
    request: Request,
    cluster_id: int,
    svc: IrrigationServiceDep,
    session: SessionDep,
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
    session.commit()
    return templates.TemplateResponse(
        request, "partials/_decision_panel.html", base_context(request, result=result, cluster_id=cluster_id)
    )


@router.get("/clusters/{cluster_id}/monitor")
def monitor(request: Request, cluster_id: int, svc: IrrigationServiceDep, session: SessionDep):
    result = svc.monitor_cluster(cluster_id=cluster_id, no_sync=True)
    session.commit()
    return templates.TemplateResponse(
        request, "partials/_monitor_panel.html", base_context(request, result=result, cluster_id=cluster_id)
    )


@router.post("/clusters/{cluster_id}/check")
def check_single(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    svc: IrrigationServiceDep,
    session: SessionDep,
):
    require_cluster(repo, cluster_id)
    result = svc.check_cluster(cluster_id)
    session.commit()
    return templates.TemplateResponse(
        request,
        "partials/_check_result.html",
        base_context(request, results=[result], has_alerts=bool(result.get("alerts"))),
    )


@router.post("/check")
def check_all(request: Request, svc: IrrigationServiceDep, session: SessionDep):
    results = svc.check_all_clusters()
    session.commit()
    has_alerts = any(r.get("alerts") for r in results)
    return templates.TemplateResponse(
        request, "partials/_check_result.html", base_context(request, results=results, has_alerts=has_alerts)
    )


@router.post("/sync")
def sync_all(request: Request, svc: SyncServiceDep, session: SessionDep, hours: str = Form("24")):
    try:
        hrs = int(hours)
    except ValueError as exc:
        raise HTTPException(400, "Invalid hours") from exc
    result = svc.sync_all_sensors(hours=hrs)
    session.commit()
    return templates.TemplateResponse(request, "partials/_sync_result.html", base_context(request, result=result))


@router.post("/plants/sync")
def sync_plants(
    request: Request,
    repo: RepoDep,
    svc: ClusterServiceDep,
    plant_id: str = Form(""),
    cluster_id: str = Form(""),
):
    errors: list[str] = []
    synced = 0
    pid = int(plant_id) if plant_id.strip() else None
    cid = int(cluster_id) if cluster_id.strip() else None

    if pid:
        plant = None
        for c in repo.list_clusters():
            for p in repo.get_plants_in_cluster(c.id):
                if p.id == pid:
                    plant = p
                    break
            if plant:
                break
        if not plant:
            raise HTTPException(404, f"Plant {pid} not found")
        svc.sync_plant_with_db(plant)
        synced = 1
    else:
        clusters = [repo.get_cluster(cid)] if cid else repo.list_clusters()
        for c in clusters:
            if not c:
                continue
            for p in repo.get_plants_in_cluster(c.id):
                try:
                    svc.sync_plant_with_db(p)
                    synced += 1
                except Exception as exc:
                    errors.append(f"{p.species}: {exc}")

    repo.session.commit()
    return templates.TemplateResponse(
        request,
        "partials/_sync_result.html",
        base_context(request, result={"synced": synced, "errors": errors, "kind": "plants"}),
    )
