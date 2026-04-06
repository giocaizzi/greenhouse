"""Operation routes: status, irrigate, check, monitor, sync, learn, history, stats."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from tuya_irrigation_core.schemas import (
    IrrigateRequest,
    IrrigationEventResponse,
    LearnResponse,
    SensorReadingResponse,
    SyncRequest,
    SyncResponse,
)
from tuya_irrigation_core.stats import get_irrigation_stats
from tuya_irrigation_server.deps import DeviceManagerDep, RepoDep
from tuya_irrigation_server.services.cluster import get_cluster_history, get_cluster_status
from tuya_irrigation_server.services.irrigation import (
    check_all_clusters,
    check_cluster,
    monitor_cluster,
    run_irrigation_pipeline,
)
from tuya_irrigation_server.services.maintenance import collect_learning_alerts, generate_learning_report
from tuya_irrigation_server.services.sync import sync_all_sensors

router = APIRouter(tags=["operations"])


@router.get("/clusters/{cluster_id}/status")
def cluster_status(cluster_id: int, repo: RepoDep, dm: DeviceManagerDep):
    result = get_cluster_status(repo, cluster_id, dm)
    if not result:
        raise HTTPException(status_code=404, detail="Cluster not found")
    # Serialize manually since we have mixed ORM objects and dicts
    decision = result["decision"]
    return {
        "cluster": {
            "id": result["cluster"].id,
            "name": result["cluster"].name,
            "location": result["cluster"].location,
            "created_at": result["cluster"].created_at,
            "environment": result["cluster"].environment,
        },
        "config": (
            {
                "id": result["config"].id,
                "cluster_id": result["config"].cluster_id,
                "mode": result["config"].mode,
                "duration_minutes": result["config"].duration_minutes,
                "interval_hours": result["config"].interval_hours,
                "auto_run": result["config"].auto_run,
                "last_updated": result["config"].last_updated,
            }
            if result["config"]
            else None
        ),
        "plants": [
            {
                "id": p.id,
                "cluster_id": p.cluster_id,
                "species": p.species,
                "category": p.category,
                "water_needs": p.water_needs,
            }
            for p in result["plants"]
        ],
        "sensors": [
            {
                "id": s["id"],
                "name": s["name"],
                "type": s["type"],
                "plant_id": s["plant_id"],
                "last_reading": (
                    SensorReadingResponse.model_validate(s["last_reading"]).model_dump() if s["last_reading"] else None
                ),
                "reading_age_seconds": s["reading_age_seconds"],
            }
            for s in result["sensors"]
        ],
        "irrigators": [
            {
                "id": i["id"],
                "name": i["name"],
                "type": i["type"],
                "recent_event_count": i["recent_event_count"],
                "last_event": (
                    IrrigationEventResponse.model_validate(i["last_event"]).model_dump() if i["last_event"] else None
                ),
            }
            for i in result["irrigators"]
        ],
        "decision": (
            {
                "action": decision["action"],
                "reason": decision["reason"],
                "confidence": decision["confidence"],
                "duration_minutes": decision["duration_minutes"],
                "interval_hours": decision["interval_hours"],
            }
            if decision
            else None
        ),
    }


@router.post("/clusters/{cluster_id}/irrigate")
def irrigate(cluster_id: int, request: IrrigateRequest, repo: RepoDep, dm: DeviceManagerDep):
    result = run_irrigation_pipeline(
        repo,
        cluster_id,
        dm,
        temp_override=request.temp_override,
        dry_run=request.dry_run,
        no_sync=request.no_sync,
    )
    if result.get("action") == "error" and result.get("reason") == "cluster not found":
        raise HTTPException(status_code=404, detail="Cluster not found")
    return result


@router.get("/clusters/{cluster_id}/monitor")
def monitor(cluster_id: int, repo: RepoDep):
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return monitor_cluster(repo, cluster_id)


@router.post("/check")
def check_all(repo: RepoDep, dm: DeviceManagerDep):
    results = check_all_clusters(repo, dm)
    has_alerts = any(r.get("alerts") or r.get("maintenance") or r.get("needs_water") for r in results)
    return {"results": results, "has_alerts": has_alerts}


@router.post("/clusters/{cluster_id}/check")
def check_single(cluster_id: int, repo: RepoDep, dm: DeviceManagerDep):
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return check_cluster(repo, cluster_id, dm)


@router.post("/sync")
def sync(request: SyncRequest, repo: RepoDep):
    stats = sync_all_sensors(repo, hours=request.hours)
    return SyncResponse(
        total_synced=stats["total_synced"],
        total_new=stats["total_new"],
        total_live=stats["total_live"],
        errors=stats["errors"],
    )


@router.get("/clusters/{cluster_id}/learn")
def learn(cluster_id: int, repo: RepoDep):
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    report = generate_learning_report(repo, cluster_id)
    alerts = collect_learning_alerts(repo, cluster_id)
    return LearnResponse(cluster_name=cluster.name, report=report, alerts=alerts)


@router.get("/clusters/{cluster_id}/history")
def history(
    cluster_id: int,
    repo: RepoDep,
    hours: int = Query(default=24),
    limit: int = Query(default=50),
):
    result = get_cluster_history(repo, cluster_id, hours=hours, limit=limit)
    if not result:
        raise HTTPException(status_code=404, detail="Cluster not found")
    # Serialize ORM objects
    return {
        "cluster_name": result["cluster_name"],
        "sensors": [
            {
                "sensor_id": s["sensor_id"],
                "sensor_name": s["sensor_name"],
                "readings": [SensorReadingResponse.model_validate(r).model_dump() for r in s["readings"]],
            }
            for s in result["sensors"]
        ],
        "irrigators": [
            {
                "irrigator_id": i["irrigator_id"],
                "irrigator_name": i["irrigator_name"],
                "events": [IrrigationEventResponse.model_validate(e).model_dump() for e in i["events"]],
            }
            for i in result["irrigators"]
        ],
    }


@router.get("/clusters/{cluster_id}/stats")
def stats(cluster_id: int, repo: RepoDep, days: int = Query(default=7)):
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    result = get_irrigation_stats(repo, cluster_id, days)
    return {
        "cluster_name": cluster.name,
        **result,
    }


@router.get("/clusters/{cluster_id}/stats/export")
def stats_export(cluster_id: int, repo: RepoDep, days: int = Query(default=7)):
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")

    import csv
    import io

    output = io.StringIO()
    # Build CSV in memory
    writer = csv.writer(output)
    writer.writerow(["timestamp", "date", "time", "irrigator", "action", "duration_min", "trigger", "notes"])
    irrigators = repo.get_irrigators_in_cluster(cluster_id)
    for irrigator in irrigators:
        events = repo.get_recent_events(irrigator.id, hours=days * 24)
        for event in events:
            from tuya_irrigation_core.utils import format_timestamp

            ts_str = format_timestamp(event.timestamp)
            writer.writerow(
                [
                    event.timestamp,
                    ts_str.split(" ")[0] if " " in ts_str else ts_str,
                    ts_str.split(" ")[1] if " " in ts_str else "",
                    irrigator.name,
                    event.action,
                    event.duration_minutes or "",
                    event.triggered_by,
                    event.notes or "",
                ]
            )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=cluster_{cluster_id}_stats.csv"},
    )
