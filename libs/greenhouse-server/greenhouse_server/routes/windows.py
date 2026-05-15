"""Per-cluster irrigation window CRUD.

Windows declare when a cluster is *allowed* to water in local time. The
decision engine checks them after cooldown but before stress overrides — a
plant in genuine stress still gets water at 2am.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from greenhouse_core.schemas import (
    CreateIrrigationWindowRequest,
    IrrigationWindowListResponse,
    IrrigationWindowResponse,
    SuccessResponse,
    UpdateIrrigationWindowRequest,
)
from greenhouse_server.deps import RepoDep, require_cluster

router = APIRouter(tags=["windows"])


def _validate_hours(start: int, end: int) -> None:
    if not (0 <= start <= 23 and 0 <= end <= 23):
        raise HTTPException(status_code=400, detail="start_hour and end_hour must be 0..23")
    if start == end:
        raise HTTPException(status_code=400, detail="start_hour and end_hour must differ")


def _validate_weekday_mask(mask: int) -> None:
    if not (1 <= mask <= 127):
        raise HTTPException(status_code=400, detail="weekday_mask must be 1..127 (Mon=1, Sun=64)")


@router.get(
    "/clusters/{cluster_id}/windows",
    response_model=IrrigationWindowListResponse,
    summary="List a cluster's irrigation windows",
)
def list_windows(cluster_id: int, repo: RepoDep):
    """Return every configured watering window for the cluster, oldest first.

    Args:
        cluster_id: Cluster the windows belong to.

    Returns:
        ``IrrigationWindowListResponse`` — may have an empty ``windows`` list
        when no per-cluster windows are configured (the engine then falls back
        to the global default preferred hours).

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    require_cluster(repo, cluster_id)
    rows = repo.list_irrigation_windows(cluster_id)
    return IrrigationWindowListResponse(
        cluster_id=cluster_id,
        windows=[IrrigationWindowResponse.model_validate(r) for r in rows],
    )


@router.post(
    "/clusters/{cluster_id}/windows",
    response_model=IrrigationWindowResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an irrigation window",
)
def add_window(cluster_id: int, request: CreateIrrigationWindowRequest, repo: RepoDep):
    """Register a new local-time window during which this cluster may irrigate.

    Multiple windows per cluster are allowed (e.g. a morning + a backup
    evening). Half-open: ``start_hour`` inclusive, ``end_hour`` exclusive.
    Wrap-around windows (start > end) cross midnight.

    Args:
        cluster_id: Cluster the window belongs to.
        request: Window definition — start/end hour, weekday bitmask, label.

    Returns:
        The persisted window.

    Raises:
        HTTPException: 404 if the cluster does not exist, 400 if hours or mask
            are out of range.
    """
    require_cluster(repo, cluster_id)
    _validate_hours(request.start_hour, request.end_hour)
    _validate_weekday_mask(request.weekday_mask)
    row = repo.add_irrigation_window(
        cluster_id,
        start_hour=request.start_hour,
        end_hour=request.end_hour,
        weekday_mask=request.weekday_mask,
        label=request.label,
    )
    repo.session.commit()
    return IrrigationWindowResponse.model_validate(row)


@router.put(
    "/clusters/{cluster_id}/windows/{window_id}",
    response_model=IrrigationWindowResponse,
    summary="Update an irrigation window",
)
def update_window(cluster_id: int, window_id: int, request: UpdateIrrigationWindowRequest, repo: RepoDep):
    """Patch a window's hours, weekday mask, or label.

    Args:
        cluster_id: Cluster the window belongs to.
        window_id: Numeric window identifier.
        request: Any subset of start_hour, end_hour, weekday_mask, label.

    Returns:
        The updated window.

    Raises:
        HTTPException: 404 if the window does not exist or belongs to a
            different cluster, 400 if the resulting hours or mask are invalid.
    """
    row = repo.get_irrigation_window(window_id)
    if row is None or row.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="Window not found in cluster")
    # Validate the effective post-patch values, not the raw partial payload.
    effective_start = request.start_hour if request.start_hour is not None else row.start_hour
    effective_end = request.end_hour if request.end_hour is not None else row.end_hour
    effective_mask = request.weekday_mask if request.weekday_mask is not None else row.weekday_mask
    _validate_hours(effective_start, effective_end)
    _validate_weekday_mask(effective_mask)
    updated = repo.update_irrigation_window(window_id, **request.model_dump(exclude_none=True))
    repo.session.commit()
    return IrrigationWindowResponse.model_validate(updated)


@router.delete(
    "/clusters/{cluster_id}/windows/{window_id}",
    response_model=SuccessResponse,
    summary="Delete an irrigation window",
)
def delete_window(cluster_id: int, window_id: int, repo: RepoDep):
    """Remove a watering window.

    Args:
        cluster_id: Cluster the window belongs to.
        window_id: Numeric window identifier.

    Returns:
        ``success=True``.

    Raises:
        HTTPException: 404 if the window does not exist or belongs to a
            different cluster.
    """
    row = repo.get_irrigation_window(window_id)
    if row is None or row.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="Window not found in cluster")
    repo.delete_irrigation_window(window_id)
    repo.session.commit()
    return SuccessResponse(success=True)
