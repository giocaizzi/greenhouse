"""Operation routes: status, irrigate, check, monitor, sync, learn, history, stats."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from tuya_irrigation_core.schemas import (
    CheckAllResponse,
    CheckClusterResponse,
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
    MonitorResponse,
    PlantResponse,
    SensorHistoryResponse,
    SensorReadingResponse,
    SensorStatusResponse,
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
    """Full cluster snapshot: config, plants, sensors (latest reading + age),
    irrigators (last event), and the current decision-engine recommendation.

    Read-only — does not actuate hardware or modify the database.

    Args:
        cluster_id: Cluster to inspect.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
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


@router.post("/clusters/{cluster_id}/irrigate", response_model=IrrigateResponse)
def irrigate(
    cluster_id: int,
    request: IrrigateRequest,
    irrigation_svc: IrrigationServiceDep,
    session: SessionDep,
) -> IrrigateResponse:
    """Run the full smart-irrigation pipeline for a cluster.

    Sequence: sync sensors → resolve temperature (sensor / weather / fallback)
    → run the decision engine → if not skipped and not a dry run, actuate the
    cluster's irrigator and record an `auto`-triggered event. Honors the
    cluster's irrigation config and the global 6h cooldown.

    Side effects: actuates physical hardware unless `dry_run=True`; writes a
    sensor sync and an irrigation event.

    Args:
        cluster_id: Cluster to irrigate.
        request: `temp_override` to bypass sensor/weather resolution,
            `dry_run=True` to compute the decision without actuating,
            `no_sync=True` to skip the sensor refresh.

    Returns:
        Decision details (`action`, `reason`, `confidence`, optional
        `duration_minutes`/`interval_hours`, plus the temperature used and its
        source). `action` is one of `irrigated`, `skip`, or `error`.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    result = irrigation_svc.run_irrigation_pipeline(
        cluster_id,
        temp_override=request.temp_override,
        dry_run=request.dry_run,
        no_sync=request.no_sync,
    )
    if result.get("action") == "error" and result.get("reason") == "cluster not found":
        raise HTTPException(status_code=404, detail="Cluster not found")
    session.commit()
    return IrrigateResponse(**result)


@router.get("/clusters/{cluster_id}/monitor", response_model=MonitorResponse)
def monitor(cluster_id: int, repo: RepoDep, irrigation_svc: IrrigationServiceDep) -> MonitorResponse:
    """Per-sensor soil-moisture status for a cluster, plus a list of plants
    that currently need water.

    Use this for sensor-only clusters (no irrigators) where you want to know
    which plants are dry without running the decision engine. Read-only.

    Args:
        cluster_id: Cluster to monitor.

    Returns:
        Each sensor's current soil moisture, the plant's target band, and a
        status (`ok` / `dry` / `very_dry` / `wet` / `no_data`), plus a flat
        `needs_water` list of plant labels for the dry/very_dry sensors.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    require_cluster(repo, cluster_id)
    result = irrigation_svc.monitor_cluster(cluster_id)
    return MonitorResponse(
        cluster_name=result["cluster_name"],
        sensors=[SensorStatusResponse(**s) for s in result["sensors"]],
        needs_water=result["needs_water"],
    )


@router.post("/check", response_model=CheckAllResponse)
def check_all(irrigation_svc: IrrigationServiceDep, session: SessionDep) -> CheckAllResponse:
    """Run a check across every cluster.

    For each cluster: irrigate (if it has irrigators and `auto_run` is on) or
    monitor (sensor-only). Collects learning + maintenance alerts per cluster.

    Side effects: may actuate physical hardware on any cluster whose decision
    engine says `irrigate` and whose config has `auto_run=True`. This is the
    same call the background scheduler makes every 6 hours.

    Returns:
        Per-cluster results plus a `has_alerts` flag that is true if any
        cluster has alerts, maintenance items, or thirsty plants.
    """
    results = irrigation_svc.check_all_clusters()
    has_alerts = any(r.get("alerts") or r.get("maintenance") or r.get("needs_water") for r in results)
    session.commit()
    return CheckAllResponse(
        results=[CheckClusterResponse(**r) for r in results],
        has_alerts=has_alerts,
    )


@router.post("/clusters/{cluster_id}/check", response_model=CheckClusterResponse)
def check_single(
    cluster_id: int,
    repo: RepoDep,
    irrigation_svc: IrrigationServiceDep,
    session: SessionDep,
) -> CheckClusterResponse:
    """Run a check for a single cluster (irrigate or monitor + collect alerts).

    Side effects: may actuate physical hardware if the cluster has irrigators
    and `auto_run=True`. Same semantics as POST /check, scoped to one cluster.

    Args:
        cluster_id: Cluster to check.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    require_cluster(repo, cluster_id)
    result = irrigation_svc.check_cluster(cluster_id)
    session.commit()
    return CheckClusterResponse(**result)


@router.post("/sync", response_model=SyncResponse)
def sync(request: SyncRequest, sync_svc: SyncServiceDep, session: SessionDep) -> SyncResponse:
    """Pull recent sensor readings from the Tuya Cloud into the local SQLite archive.

    This is the same job the background scheduler runs every 30 minutes; call
    this manually after registering a new sensor or to backfill after an
    outage. No hardware is actuated.

    Args:
        request: `hours` of look-back to fetch (default 24).

    Returns:
        Counts of total readings synced, new vs. duplicate-skipped, live
        readings hit, and any per-sensor error messages.
    """
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
    """Human-readable learning report for a cluster.

    Summarises absorption rates, drainage profiles, and any advisory alerts
    (blocked drip, rapid drainage, chronic underwatering, etc.) the learner
    has detected. Read-only and never blocks irrigation.

    Args:
        cluster_id: Cluster to analyse.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
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
    """Recent sensor readings and irrigation events for a cluster.

    Args:
        cluster_id: Cluster to inspect.
        hours: Look-back window in hours (default 24).
        limit: Maximum readings/events per sensor or irrigator (default 50).

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
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
    """Aggregate irrigation statistics for a cluster.

    Args:
        cluster_id: Cluster to compute stats for.
        days: Look-back window in days (default 7).

    Returns:
        Total event count, total + average duration, frequency per day, and
        breakdowns by event type and trigger source.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    cluster = require_cluster(repo, cluster_id)
    result = get_irrigation_stats(repo, cluster_id, days)
    return StatsResponse(cluster_name=cluster.name, **result)


@router.get("/clusters/{cluster_id}/stats/export")
def stats_export(cluster_id: int, repo: RepoDep, days: int = Query(default=7, ge=1)):
    """Export raw irrigation events for a cluster as a CSV download.

    The CSV columns are: timestamp, date, time, irrigator, action,
    duration_minutes, triggered_by, notes. This route returns a binary
    `text/csv` stream and is therefore exempt from the response_model
    typing requirement that the rest of the API follows.

    Args:
        cluster_id: Cluster to export.
        days: Look-back window in days (default 7).

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    cluster = require_cluster(repo, cluster_id)

    import csv
    import io

    from tuya_irrigation_core.utils import format_timestamp

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "date", "time", "irrigator", "action", "duration_minutes", "triggered_by", "notes"])
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
