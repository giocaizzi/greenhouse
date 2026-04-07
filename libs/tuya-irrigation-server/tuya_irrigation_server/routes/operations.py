"""Operation routes: status, irrigate, check, monitor, sync, learn, history, stats."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from tuya_irrigation_core.schemas import (
    ClusterStatusIrrigatorResponse,
    ClusterStatusResponse,
    ClusterStatusSensorResponse,
    ConfigResponse,
    HistoryResponse,
    IrrigateRequest,
    IrrigateResponse,
    IrrigationEventResponse,
    IrrigatorHistoryResponse,
    LearnResponse,
    PlantResponse,
    SensorHistoryResponse,
    SensorReadingResponse,
    StatsResponse,
    SyncRequest,
    SyncResponse,
)
from tuya_irrigation_core.stats import get_irrigation_stats
from tuya_irrigation_server.deps import (
    ClusterServiceDep,
    IrrigationServiceDep,
    PlantDbDep,
    RepoDep,
    SessionDep,
    SyncServiceDep,
    require_cluster,
)
from tuya_irrigation_server.services.maintenance import collect_learning_alerts, generate_learning_report

router = APIRouter(tags=["operations"])


@router.get("/clusters/{cluster_id}/status", response_model=ClusterStatusResponse)
def cluster_status(cluster_id: int, cluster_svc: ClusterServiceDep):
    result = cluster_svc.get_cluster_status(cluster_id)
    if not result:
        raise HTTPException(status_code=404, detail="Cluster not found")

    config = result["config"]
    decision = result["decision"]

    return ClusterStatusResponse(
        cluster=result["cluster"],
        config=ConfigResponse.model_validate(config) if config else None,
        plants=[PlantResponse.model_validate(p) for p in result["plants"]],
        sensors=[
            ClusterStatusSensorResponse(
                id=s["id"],
                name=s["name"],
                type=s["type"],
                plant_id=s["plant_id"],
                last_reading=SensorReadingResponse.model_validate(s["last_reading"]) if s["last_reading"] else None,
                reading_age_seconds=s["reading_age_seconds"],
            )
            for s in result["sensors"]
        ],
        irrigators=[
            ClusterStatusIrrigatorResponse(
                id=i["id"],
                name=i["name"],
                type=i["type"],
                recent_event_count=i["recent_event_count"],
                last_event=IrrigationEventResponse.model_validate(i["last_event"]) if i["last_event"] else None,
            )
            for i in result["irrigators"]
        ],
        decision=(
            IrrigateResponse(
                action=decision["action"],
                reason=decision["reason"],
                confidence=decision["confidence"],
                duration_minutes=decision["duration_minutes"],
                interval_hours=decision["interval_hours"],
            )
            if decision
            else None
        ),
    )


@router.post("/clusters/{cluster_id}/irrigate")
def irrigate(cluster_id: int, request: IrrigateRequest, irrigation_svc: IrrigationServiceDep, session: SessionDep):
    result = irrigation_svc.run_irrigation_pipeline(
        cluster_id,
        temp_override=request.temp_override,
        dry_run=request.dry_run,
        no_sync=request.no_sync,
    )
    if result.get("action") == "error" and result.get("reason") == "cluster not found":
        raise HTTPException(status_code=404, detail="Cluster not found")
    session.commit()
    return result


@router.get("/clusters/{cluster_id}/monitor")
def monitor(cluster_id: int, repo: RepoDep, irrigation_svc: IrrigationServiceDep):
    require_cluster(repo, cluster_id)
    return irrigation_svc.monitor_cluster(cluster_id)


@router.post("/check")
def check_all(irrigation_svc: IrrigationServiceDep, session: SessionDep):
    results = irrigation_svc.check_all_clusters()
    has_alerts = any(r.get("alerts") or r.get("maintenance") or r.get("needs_water") for r in results)
    session.commit()
    return {"results": results, "has_alerts": has_alerts}


@router.post("/clusters/{cluster_id}/check")
def check_single(cluster_id: int, repo: RepoDep, irrigation_svc: IrrigationServiceDep, session: SessionDep):
    require_cluster(repo, cluster_id)
    result = irrigation_svc.check_cluster(cluster_id)
    session.commit()
    return result


@router.post("/sync")
def sync(request: SyncRequest, sync_svc: SyncServiceDep, session: SessionDep):
    stats = sync_svc.sync_all_sensors(hours=request.hours)
    session.commit()
    return SyncResponse(
        total_synced=stats["total_synced"],
        total_new=stats["total_new"],
        total_live=stats["total_live"],
        errors=stats["errors"],
    )


@router.get("/clusters/{cluster_id}/learn", response_model=LearnResponse)
def learn(cluster_id: int, repo: RepoDep, plant_db: PlantDbDep):
    cluster = require_cluster(repo, cluster_id)
    report = generate_learning_report(repo, cluster_id, plant_db)
    alerts = collect_learning_alerts(repo, cluster_id, plant_db)
    return LearnResponse(cluster_name=cluster.name, report=report, alerts=alerts)


@router.get("/clusters/{cluster_id}/history", response_model=HistoryResponse)
def history(
    cluster_id: int,
    cluster_svc: ClusterServiceDep,
    hours: int = Query(default=24, ge=1),
    limit: int = Query(default=50, ge=1),
):
    result = cluster_svc.get_cluster_history(cluster_id, hours=hours, limit=limit)
    if not result:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return HistoryResponse(
        cluster_name=result["cluster_name"],
        sensors=[
            SensorHistoryResponse(
                sensor_id=s["sensor_id"],
                sensor_name=s["sensor_name"],
                readings=[SensorReadingResponse.model_validate(r) for r in s["readings"]],
            )
            for s in result["sensors"]
        ],
        irrigators=[
            IrrigatorHistoryResponse(
                irrigator_id=i["irrigator_id"],
                irrigator_name=i["irrigator_name"],
                events=[IrrigationEventResponse.model_validate(e) for e in i["events"]],
            )
            for i in result["irrigators"]
        ],
    )


@router.get("/clusters/{cluster_id}/stats", response_model=StatsResponse)
def stats(cluster_id: int, repo: RepoDep, days: int = Query(default=7, ge=1)):
    cluster = require_cluster(repo, cluster_id)
    result = get_irrigation_stats(repo, cluster_id, days)
    return StatsResponse(cluster_name=cluster.name, **result)


@router.get("/clusters/{cluster_id}/stats/export")
def stats_export(cluster_id: int, repo: RepoDep, days: int = Query(default=7, ge=1)):
    cluster = require_cluster(repo, cluster_id)

    import csv
    import io

    from tuya_irrigation_core.utils import format_timestamp

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "date", "time", "irrigator", "action", "duration_min", "trigger", "notes"])
    irrigators = repo.get_irrigators_in_cluster(cluster_id)
    for irrigator in irrigators:
        events = repo.get_recent_events(irrigator.id, hours=days * 24)
        for event in events:
            ts_str = format_timestamp(event.timestamp)
            date, _, time_part = ts_str.partition(" ")
            writer.writerow(
                [
                    event.timestamp,
                    date,
                    time_part,
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
        headers={"Content-Disposition": f"attachment; filename=cluster_{cluster.id}_stats.csv"},
    )
