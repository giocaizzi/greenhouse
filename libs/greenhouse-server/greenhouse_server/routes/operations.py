"""Operation routes: status, irrigate, check, monitor, sync, learn, history, stats."""

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from greenhouse_core.schemas import (
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
from greenhouse_core.stats import get_irrigation_stats
from greenhouse_server.deps import (
    ClusterServiceDep,
    IrrigationServiceDep,
    PlantDbDep,
    RepoDep,
    SyncServiceDep,
    not_found_as_404,
    require_cluster,
)
from greenhouse_server.services.cluster import cluster_events_csv
from greenhouse_server.services.irrigation import check_has_alerts
from greenhouse_server.services.maintenance import collect_learning_alerts, generate_learning_report

router = APIRouter(tags=["operations"])

# GET /clusters/{id}/stats for a cluster without an irrigator: the documented shape, all zero.
_NO_IRRIGATION_STATS = {
    "total_events": 0,
    "total_duration_minutes": 0,
    "avg_duration_minutes": 0.0,
    "frequency_per_day": 0.0,
    "events_by_type": {},
    "events_by_trigger": {},
}


@router.get("/clusters/{cluster_id}/status", response_model=ClusterStatusResponse)
def cluster_status(cluster_id: int, cluster_svc: ClusterServiceDep) -> ClusterStatusResponse:
    """Return a full cluster snapshot with the current decision-engine recommendation.

    The snapshot covers the config, plants, sensors (latest reading + age) and
    the irrigator (last event). Read-only — does not actuate hardware or modify
    the database.

    Args:
        cluster_id: Cluster to inspect.

    Returns:
        The cluster, its config, plants, sensors, irrigator, and the decision
        the engine would make now (`null` parts when absent).

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    with not_found_as_404("Cluster not found"):
        result = cluster_svc.get_cluster_status(cluster_id)

    config = result["config"]
    decision = result["decision"]

    return ClusterStatusResponse(
        cluster=result["cluster"],
        config=ConfigResponse.model_validate(config) if config else None,
        plants=[PlantResponse.model_validate(p) for p in result["plants"]],
        sensors=[_status_sensor(s) for s in result["sensors"]],
        irrigator=_status_irrigator(result["irrigator"]) if result["irrigator"] else None,
        decision=_status_decision(decision) if decision else None,
    )


def _status_sensor(s: dict[str, Any]) -> ClusterStatusSensorResponse:
    """Map one ``get_cluster_status`` sensor row to its response model."""
    return ClusterStatusSensorResponse(
        id=s["id"],
        name=s["name"],
        type=s["type"],
        plant_id=s["plant_id"],
        last_reading=SensorReadingResponse.model_validate(s["last_reading"]) if s["last_reading"] else None,
        reading_age_seconds=s["reading_age_seconds"],
    )


def _status_irrigator(irrigator: dict[str, Any]) -> ClusterStatusIrrigatorResponse:
    """Map the ``get_cluster_status`` irrigator dict to its response model."""
    return ClusterStatusIrrigatorResponse(
        id=irrigator["id"],
        name=irrigator["name"],
        type=irrigator["type"],
        recent_event_count=irrigator["recent_event_count"],
        last_event=(
            IrrigationEventResponse.model_validate(irrigator["last_event"]) if irrigator["last_event"] else None
        ),
    )


def _status_decision(decision: dict[str, Any]) -> IrrigateResponse:
    """Map the ``get_cluster_status`` decision view to the irrigate response model."""
    return IrrigateResponse(
        action=decision["action"],
        reason=decision["reason"],
        confidence=decision["confidence"],
        duration_minutes=decision["duration_minutes"],
        interval_hours=decision["interval_hours"],
        stress_indicators=decision.get("stress_indicators"),
        reasons=decision.get("reasons", []),
    )


@router.post("/clusters/{cluster_id}/irrigate", response_model=IrrigateResponse)
def irrigate(
    cluster_id: int,
    request: IrrigateRequest,
    irrigation_svc: IrrigationServiceDep,
    repo: RepoDep,
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
            `no_sync=True` to skip the sensor refresh, `force=True` to
            bypass the quiet-hours deny window (the decision still records
            a `manual_override_quiet_hours` warning Reason).

    Returns:
        Decision details (`action`, `reason`, `confidence`, optional
        `duration_minutes`/`interval_hours`, plus the temperature used and its
        source). `action` is one of `irrigated`, `skip`, or `error`.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    require_cluster(repo, cluster_id)
    result = irrigation_svc.run_irrigation_pipeline(
        cluster_id,
        temp_override=request.temp_override,
        dry_run=request.dry_run,
        no_sync=request.no_sync,
        force=request.force,
    )
    repo.commit()
    return IrrigateResponse.model_validate(result)


@router.get("/clusters/{cluster_id}/monitor", response_model=MonitorResponse)
def monitor(cluster_id: int, repo: RepoDep, irrigation_svc: IrrigationServiceDep) -> MonitorResponse:
    """Report each sensor's soil-moisture status and the plants that currently need water.

    Use this for sensor-only clusters (no irrigators) where you want to know
    which plants are dry without running the decision engine. Never actuates;
    sensors whose latest reading is stale are first refreshed from the Tuya
    Cloud and the refreshed readings are stored.

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
    repo.commit()  # keep the rows the freshness sync just stored
    return MonitorResponse(
        cluster_name=result["cluster_name"],
        sensors=[SensorStatusResponse.model_validate(s) for s in result["sensors"]],
        needs_water=result["needs_water"],
    )


@router.post("/check", response_model=CheckAllResponse)
def check_all(irrigation_svc: IrrigationServiceDep, repo: RepoDep) -> CheckAllResponse:
    """Run a check across every cluster.

    For each cluster: irrigate (if it has irrigators and `auto_run` is on) or
    monitor (sensor-only). Collects learning + maintenance alerts per cluster.

    Side effects: may actuate physical hardware on any cluster whose decision
    engine says `irrigate` and whose config has `auto_run=True`. This is the
    same call the background `check_all` job makes on its cron schedule
    (`IRRIGATION_CHECK_CRON_HOURS`, default hourly at :00).

    Returns:
        Per-cluster results plus a `has_alerts` flag that is true if any
        cluster has alerts, maintenance items, or thirsty plants.
    """
    results = irrigation_svc.check_all_clusters()
    has_alerts = check_has_alerts(results)
    repo.commit()
    return CheckAllResponse(
        results=[CheckClusterResponse.model_validate(r) for r in results],
        has_alerts=has_alerts,
    )


@router.post("/clusters/{cluster_id}/check", response_model=CheckClusterResponse)
def check_single(
    cluster_id: int,
    repo: RepoDep,
    irrigation_svc: IrrigationServiceDep,
) -> CheckClusterResponse:
    """Run a check for a single cluster (irrigate or monitor + collect alerts).

    Side effects: may actuate physical hardware if the cluster has irrigators
    and `auto_run=True`. Same semantics as POST /check, scoped to one cluster.

    Args:
        cluster_id: Cluster to check.

    Returns:
        The cluster's check result: irrigation or monitor outcome plus its
        learning and maintenance alerts.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    require_cluster(repo, cluster_id)
    result = irrigation_svc.check_cluster(cluster_id)
    repo.commit()
    return CheckClusterResponse.model_validate(result)


@router.post("/sync", response_model=SyncResponse)
def sync(request: SyncRequest, sync_svc: SyncServiceDep, repo: RepoDep) -> SyncResponse:
    """Pull recent sensor readings from the Tuya Cloud into the local SQLite archive.

    This is the same job the background scheduler runs every
    `IRRIGATION_SYNC_INTERVAL_MINUTES` (default 180); call
    this manually after registering a new sensor or to backfill after an
    outage. No hardware is actuated.

    Args:
        request: `hours` of look-back to fetch (default 24).

    Returns:
        Counts of total readings synced, new vs. duplicate-skipped, live
        readings hit, and any per-sensor error messages.
    """
    stats = sync_svc.sync_all_sensors(hours=request.hours)
    repo.commit()
    return SyncResponse(
        total_synced=stats["total_synced"],
        total_new=stats["total_new"],
        total_live=stats["total_live"],
        errors=stats["errors"],
    )


@router.get("/clusters/{cluster_id}/learn", response_model=LearnResponse)
def learn(cluster_id: int, repo: RepoDep, plant_db: PlantDbDep) -> LearnResponse:
    """Return a human-readable learning report for a cluster.

    Summarises absorption rates, drainage profiles, and any advisory alerts
    (blocked drip, rapid drainage, chronic underwatering, etc.) the learner
    has detected. Read-only and never blocks irrigation.

    Args:
        cluster_id: Cluster to analyse.

    Returns:
        The cluster name, the report text, and the advisory learning alerts.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    cluster = require_cluster(repo, cluster_id)
    report = generate_learning_report(repo, cluster_id, plant_db)
    alerts = collect_learning_alerts(repo, cluster_id, plant_db)
    return LearnResponse.model_validate({"cluster_name": cluster.name, "report": report, "alerts": alerts})


@router.get("/clusters/{cluster_id}/history", response_model=HistoryResponse)
def history(
    cluster_id: int,
    cluster_svc: ClusterServiceDep,
    hours: int = Query(default=24, ge=1),
    limit: int = Query(default=50, ge=1),
) -> HistoryResponse:
    """Return recent sensor readings and irrigation events for a cluster.

    Args:
        cluster_id: Cluster to inspect.
        hours: Look-back window in hours (default 24).
        limit: Maximum readings/events per sensor or irrigator (default 50).

    Returns:
        Per-sensor readings and per-irrigator events within the window.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    with not_found_as_404("Cluster not found"):
        result = cluster_svc.get_cluster_history(cluster_id, hours=hours, limit=limit)
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
def stats(cluster_id: int, repo: RepoDep, days: int = Query(default=7, ge=1)) -> StatsResponse:
    """Return aggregate irrigation statistics for a cluster.

    Args:
        cluster_id: Cluster to compute stats for.
        days: Look-back window in days (default 7).

    Returns:
        Total event count, total + average duration, frequency per day, and
        breakdowns by event type and trigger source. A cluster without an
        irrigator reports zero totals and empty breakdowns.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    cluster = require_cluster(repo, cluster_id)
    result = get_irrigation_stats(repo, cluster_id, days)
    if "error" in result:  # no irrigator: nothing was irrigated in the window
        return StatsResponse.model_validate({"cluster_name": cluster.name, **_NO_IRRIGATION_STATS, "period_days": days})
    return StatsResponse.model_validate({"cluster_name": cluster.name, **result})


@router.get("/clusters/{cluster_id}/stats/export")
def stats_export(cluster_id: int, repo: RepoDep, days: int = Query(default=7, ge=1)) -> StreamingResponse:
    """Export raw irrigation events for a cluster as a CSV download.

    The CSV columns are: timestamp, date, time, irrigator, action,
    duration_minutes, triggered_by, notes. This route returns a binary
    `text/csv` stream and is therefore exempt from the response_model
    typing requirement that the rest of the API follows.

    Args:
        cluster_id: Cluster to export.
        days: Look-back window in days (default 7).

    Returns:
        A `text/csv` attachment named `cluster_<id>_stats.csv`.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    cluster = require_cluster(repo, cluster_id)
    csv_text = cluster_events_csv(repo, cluster_id, days=days)
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=cluster_{cluster.id}_stats.csv"},
    )
