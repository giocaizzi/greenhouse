"""Irrigator CRUD + control routes."""

from fastapi import APIRouter, HTTPException, Query, status

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
from greenhouse_server.deps import (
    DeviceRegistryDep,
    NtfyNotifierDep,
    RepoDep,
    require_cluster,
    require_cluster_irrigator,
    require_irrigator,
)
from greenhouse_server.services.inventory import DeviceIdExistsError, IrrigatorExistsError, create_irrigator
from greenhouse_server.services.manual_control import (
    ManualActionError,
    manual_log,
    manual_start,
    manual_stop,
)

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
        request: Tuya device ID, irrigator name, type (the device model key,
            e.g. `rainpoint.ik10pw`),
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
        irrigator_id = create_irrigator(
            repo,
            cluster_id,
            tuya_device_id=request.tuya_device_id,
            name=request.name,
            irrigator_type=request.type,
            config=request.config or {},
            reservoir_l=request.reservoir_l,
            flow_rate_l_per_min=request.flow_rate_l_per_min,
        )
    except IrrigatorExistsError:
        raise HTTPException(status_code=409, detail="Cluster already has an irrigator") from None
    except DeviceIdExistsError:
        raise HTTPException(status_code=409, detail="Device ID already exists") from None
    repo.commit()
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
    irrigator = require_cluster_irrigator(repo, cluster_id)
    return irrigator


@router.put(
    "/clusters/{cluster_id}/irrigator",
    response_model=IrrigatorResponse,
    summary="Update the cluster's irrigator",
)
def update_irrigator(cluster_id: int, request: UpdateIrrigatorRequest, repo: RepoDep):
    """Partially update the metadata of the cluster's irrigator.

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
    irrigator = require_cluster_irrigator(repo, cluster_id)
    updated = repo.update_irrigator(irrigator.id, **request.model_dump(exclude_none=True))
    repo.commit()
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
    irrigator = require_cluster_irrigator(repo, cluster_id)
    repo.delete_irrigator(irrigator.id)
    repo.commit()
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

    Returns:
        `success=True` and the adapter's start message.

    Raises:
        HTTPException: 404 if the irrigator is unknown, 409 if the cluster
            daily cap or max-events-per-day limit would be exceeded, 503 if
            Tuya credentials are missing or the irrigator model has no
            adapter, 502 if the device fails to start.
    """
    irrigator = require_irrigator(repo, irrigator_id)
    try:
        output = manual_start(repo, registry, notifier, irrigator, request.minutes, via="API")
    except ManualActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return IrrigatorActionResponse(success=True, message=output)


@router.post("/irrigators/{irrigator_id}/stop", response_model=IrrigatorActionResponse)
def stop_irrigator(
    irrigator_id: int, repo: RepoDep, registry: DeviceRegistryDep, notifier: NtfyNotifierDep
) -> IrrigatorActionResponse:
    """Manually stop a running irrigator over the Tuya local protocol.

    Side effects: actuates physical hardware and records a `stop` irrigation
    event with `triggered_by="manual"` (the same action automatic and
    emergency stops record).

    Args:
        irrigator_id: Irrigator to stop.

    Returns:
        `success=True` and the adapter's stop message.

    Raises:
        HTTPException: 404 if the irrigator is unknown, 503 if Tuya
            credentials are missing or the irrigator model has no adapter,
            502 if the device fails to stop.
    """
    irrigator = require_irrigator(repo, irrigator_id)
    try:
        output = manual_stop(repo, registry, notifier, irrigator, via="API")
    except ManualActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return IrrigatorActionResponse(success=True, message=output)


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

    Returns:
        `success=True` and the id of the recorded irrigation event.

    Raises:
        HTTPException: 404 if the irrigator is unknown, 409 if the cluster
            daily cap or max-events-per-day limit would be exceeded.
    """
    irrigator = require_irrigator(repo, irrigator_id)
    try:
        event_id = manual_log(repo, notifier, irrigator, request.minutes, request.notes)
    except ManualActionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    return LogManualResponse(success=True, event_id=event_id)
