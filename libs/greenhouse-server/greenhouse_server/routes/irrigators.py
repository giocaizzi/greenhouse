"""Irrigator CRUD + control routes."""

import time

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from greenhouse_core.devices import UnknownDeviceModel
from greenhouse_core.repository import IrrigationRepository, IrrigatorExistsError
from greenhouse_core.schemas import (
    CreateIrrigatorRequest,
    IrrigatorActionResponse,
    IrrigatorListResponse,
    IrrigatorResponse,
    LogManualRequest,
    LogManualResponse,
    StartIrrigatorRequest,
    SuccessResponse,
    UpdateIrrigatorRequest,
)
from greenhouse_server.deps import DeviceRegistryDep, NtfyNotifierDep, RepoDep, require_cluster
from greenhouse_server.services.irrigation import schedule_pump_watcher
from greenhouse_server.services.notify import maybe_notify

router = APIRouter(tags=["irrigators"])


@router.get("/irrigators", response_model=IrrigatorListResponse, summary="List irrigators across all clusters")
def list_all_irrigators(
    repo: RepoDep,
    cluster_id: int | None = Query(default=None, description="Restrict results to a specific cluster"),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: int | None = Query(default=None, description="Id cursor — return rows with id > cursor"),
):
    """List every irrigator across all clusters with optional cluster filter and cursor pagination.

    Args:
        cluster_id: Restrict to a single cluster.
        limit: Page size (default 100, max 500).
        cursor: Id-based cursor — pass the previous response's ``next_cursor``
            to fetch the next page.

    Returns:
        The page of irrigators plus a ``next_cursor`` (None when the page was
        not full and there are no more rows to fetch).
    """
    rows = repo.list_all_irrigators(filter_cluster_id=cluster_id, limit=limit, after_id=cursor)
    next_cursor = rows[-1].id if len(rows) == limit else None
    return IrrigatorListResponse(
        irrigators=[IrrigatorResponse.model_validate(r) for r in rows],
        next_cursor=next_cursor,
    )


def _check_rate_limits(repo: IrrigationRepository, cluster_id: int, irrigator_id: int, minutes: int | None) -> None:
    """Raise 409 if daily-cap or max-events-per-day thresholds are exceeded.

    Args:
        repo: Active repository session.
        cluster_id: Cluster whose config holds the caps.
        irrigator_id: Irrigator being started (duration cap is per-irrigator).
        minutes: Requested duration; ``None`` counts as 0 for the duration cap.

    Raises:
        HTTPException: 409 if ``max_events_per_day`` or ``daily_cap_minutes``
            would be exceeded.
    """
    config = repo.get_irrigation_config(cluster_id)
    if not config:
        return

    requested = minutes or 0

    if config.max_events_per_day is not None:
        # Count "start" events in the last 24 h for the cluster's single irrigator.
        irrigator = repo.get_irrigator_for_cluster(cluster_id)
        total_starts = (
            sum(1 for e in repo.get_recent_events(irrigator.id, hours=24) if e.action == "start")
            if irrigator is not None
            else 0
        )
        if total_starts >= config.max_events_per_day:
            raise HTTPException(status_code=409, detail="cluster max_events_per_day reached")

    if config.daily_cap_minutes is not None:
        recent = repo.get_recent_events(irrigator_id, hours=24)
        minutes_used = sum(e.duration_minutes or 0 for e in recent if e.action == "start")
        if minutes_used + requested > config.daily_cap_minutes:
            raise HTTPException(status_code=409, detail="irrigator daily cap reached")


@router.post(
    "/clusters/{cluster_id}/irrigator",
    response_model=IrrigatorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register the cluster's irrigator",
)
def add_irrigator(cluster_id: int, request: CreateIrrigatorRequest, repo: RepoDep):
    """Register the Tuya irrigator for a cluster.

    A cluster has at most one irrigator (strict 0:1). Registering a second one
    is rejected.

    Args:
        cluster_id: Cluster the irrigator belongs to.
        request: Tuya device ID, irrigator name, type (e.g. `tuya_cloud`),
            optional config dict, and optional `reservoir_l` /
            `flow_rate_l_per_min` capacity used for vacation rationing.

    Returns:
        The newly created irrigator.

    Raises:
        HTTPException: 404 if the cluster does not exist, 409 if the cluster
            already has an irrigator or the Tuya device ID is already
            registered.
    """
    require_cluster(repo, cluster_id)
    try:
        irrigator_id = repo.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=request.tuya_device_id,
            name=request.name,
            irrigator_type=request.type,
            config=request.config or {},
        )
        # add_irrigator does not accept capacity columns; persist them here if supplied.
        if request.reservoir_l is not None or request.flow_rate_l_per_min is not None:
            repo.update_irrigator(
                irrigator_id,
                reservoir_l=request.reservoir_l,
                flow_rate_l_per_min=request.flow_rate_l_per_min,
            )
        repo.session.commit()
    except IrrigatorExistsError:
        repo.session.rollback()
        raise HTTPException(status_code=409, detail="Cluster already has an irrigator") from None
    except IntegrityError:
        repo.session.rollback()
        raise HTTPException(status_code=409, detail="Device ID already exists") from None
    return repo.get_irrigator(irrigator_id)


@router.get(
    "/clusters/{cluster_id}/irrigator",
    response_model=IrrigatorResponse,
    summary="Get the cluster's irrigator",
)
def get_irrigator(cluster_id: int, repo: RepoDep):
    """Fetch the cluster's single irrigator.

    Args:
        cluster_id: Cluster whose irrigator to fetch.

    Returns:
        The cluster's irrigator record.

    Raises:
        HTTPException: 404 if the cluster has no irrigator.
    """
    irrigator = repo.get_irrigator_for_cluster(cluster_id)
    if not irrigator:
        raise HTTPException(status_code=404, detail="Cluster has no irrigator")
    return irrigator


@router.put(
    "/clusters/{cluster_id}/irrigator",
    response_model=IrrigatorResponse,
    summary="Update the cluster's irrigator",
)
def update_irrigator(cluster_id: int, request: UpdateIrrigatorRequest, repo: RepoDep):
    """Partially update the cluster's irrigator metadata.

    Only fields present in the request body are modified; omitted fields are
    left unchanged.

    Args:
        cluster_id: Cluster whose irrigator to update.
        request: Fields to update — any subset of `name`, `type`, `config`,
            `reservoir_l`, and `flow_rate_l_per_min`.

    Returns:
        The updated irrigator.

    Raises:
        HTTPException: 404 if the cluster has no irrigator.
    """
    irrigator = repo.get_irrigator_for_cluster(cluster_id)
    if not irrigator:
        raise HTTPException(status_code=404, detail="Cluster has no irrigator")
    updated = repo.update_irrigator(irrigator.id, **request.model_dump(exclude_none=True))
    repo.session.commit()
    return updated


@router.delete(
    "/clusters/{cluster_id}/irrigator",
    response_model=SuccessResponse,
    summary="Delete the cluster's irrigator",
)
def delete_irrigator(cluster_id: int, repo: RepoDep):
    """Delete the cluster's irrigator and all its historical events.

    This operation is irreversible.

    Args:
        cluster_id: Cluster whose irrigator to delete.

    Returns:
        `{"success": true}` on successful deletion.

    Raises:
        HTTPException: 404 if the cluster has no irrigator.
    """
    irrigator = repo.get_irrigator_for_cluster(cluster_id)
    if not irrigator:
        raise HTTPException(status_code=404, detail="Cluster has no irrigator")
    repo.delete_irrigator(irrigator.id)
    repo.session.commit()
    return SuccessResponse(success=True)


@router.post("/irrigators/{irrigator_id}/start", response_model=IrrigatorActionResponse)
def start_irrigator(
    irrigator_id: int,
    request: StartIrrigatorRequest,
    repo: RepoDep,
    registry: DeviceRegistryDep,
    notifier: NtfyNotifierDep,
) -> IrrigatorActionResponse:
    """Manually start an irrigator over the Tuya local protocol.

    Side effects: actuates physical hardware and records a `start` irrigation
    event with `triggered_by="manual"`. Bypasses the smart-decision engine.

    Args:
        irrigator_id: Irrigator to actuate.
        request: Optional `minutes` for run duration.

    Raises:
        HTTPException: 404 if the irrigator is unknown, 409 if the cluster
            daily cap or max-events-per-day limit would be exceeded, 503 if
            Tuya credentials are missing or the irrigator model has no
            adapter, 502 if the device fails to start.
    """
    irrigator = repo.get_irrigator(irrigator_id)
    if not irrigator:
        raise HTTPException(status_code=404, detail="Irrigator not found")
    if registry is None:
        raise HTTPException(status_code=503, detail="No device registry (missing Tuya credentials)")

    _check_rate_limits(repo, irrigator.cluster_id, irrigator_id, request.minutes)

    try:
        adapter = registry.get_irrigator(irrigator)
    except UnknownDeviceModel as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    success, output = adapter.start(irrigator, request.minutes)
    if success:
        started_at = int(time.time())
        repo.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="start",
            duration_minutes=request.minutes,
            triggered_by="manual",
            notes=f"Manual start via API ({request.minutes} min)" if request.minutes else "Manual start via API",
            timestamp=started_at,
        )
        repo.session.commit()
        if request.minutes:
            schedule_pump_watcher(irrigator.id, request.minutes, started_at)
        maybe_notify(
            notifier,
            repo.get_preferences(),
            "manual",
            lambda: notifier.notify_irrigation(
                triggered_by="manual",
                irrigator_name=irrigator.name,
                duration_minutes=request.minutes,
                detail="started",
            ),
        )
        return IrrigatorActionResponse(success=True, message=output)
    raise HTTPException(status_code=502, detail=output)


@router.post("/irrigators/{irrigator_id}/stop", response_model=IrrigatorActionResponse)
def stop_irrigator(
    irrigator_id: int, repo: RepoDep, registry: DeviceRegistryDep, notifier: NtfyNotifierDep
) -> IrrigatorActionResponse:
    """Manually stop a running irrigator over the Tuya local protocol.

    Side effects: actuates physical hardware and records an `off` irrigation
    event with `triggered_by="manual"`.

    Args:
        irrigator_id: Irrigator to stop.

    Raises:
        HTTPException: 404 if the irrigator is unknown, 503 if Tuya
            credentials are missing or the irrigator model has no adapter,
            502 if the device fails to stop.
    """
    irrigator = repo.get_irrigator(irrigator_id)
    if not irrigator:
        raise HTTPException(status_code=404, detail="Irrigator not found")
    if registry is None:
        raise HTTPException(status_code=503, detail="No device registry (missing Tuya credentials)")

    try:
        adapter = registry.get_irrigator(irrigator)
    except UnknownDeviceModel as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    success, output = adapter.stop(irrigator)
    if success:
        repo.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="off",
            triggered_by="manual",
            notes="Manual stop via API",
        )
        repo.session.commit()
        maybe_notify(
            notifier,
            repo.get_preferences(),
            "manual",
            lambda: notifier.notify_irrigation(
                triggered_by="manual",
                irrigator_name=irrigator.name,
                detail="stopped",
            ),
        )
        return IrrigatorActionResponse(success=True, message=output)
    raise HTTPException(status_code=502, detail=output)


@router.post("/irrigators/{irrigator_id}/log-manual", response_model=LogManualResponse)
def log_manual(
    irrigator_id: int, request: LogManualRequest, repo: RepoDep, notifier: NtfyNotifierDep
) -> LogManualResponse:
    """Record a manually executed irrigation that did not go through the API.

    Useful when the user watered by hand but wants the learning engine and
    history to know about it. No hardware is actuated.

    Args:
        irrigator_id: Irrigator the manual run is attributed to.
        request: Duration in minutes plus optional notes.

    Raises:
        HTTPException: 404 if the irrigator is unknown, 409 if the cluster
            daily cap or max-events-per-day limit would be exceeded.
    """
    irrigator = repo.get_irrigator(irrigator_id)
    if not irrigator:
        raise HTTPException(status_code=404, detail="Irrigator not found")
    _check_rate_limits(repo, irrigator.cluster_id, irrigator_id, request.minutes)
    event_id = repo.add_irrigation_event(
        irrigator_id=irrigator.id,
        action="start",
        duration_minutes=request.minutes,
        triggered_by="manual",
        notes=request.notes or f"Manual ({request.minutes} min)",
    )
    repo.session.commit()
    maybe_notify(
        notifier,
        repo.get_preferences(),
        "manual",
        lambda: notifier.notify_irrigation(
            triggered_by="manual",
            irrigator_name=irrigator.name,
            duration_minutes=request.minutes,
            detail="logged (watered by hand)",
        ),
    )
    return LogManualResponse(success=True, event_id=event_id)
