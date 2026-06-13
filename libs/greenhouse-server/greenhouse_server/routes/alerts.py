"""Alert inbox routes."""

from fastapi import APIRouter, HTTPException, Query

from greenhouse_core.schemas import AlertListResponse, AlertSummary
from greenhouse_server.deps import NtfyNotifierDep, PlantDbDep, RepoDep, SessionDep
from greenhouse_server.services.alerts import sync_all_alerts, sync_cluster_alerts

router = APIRouter(tags=["alerts"])


@router.get("/alerts", response_model=AlertListResponse)
def list_alerts(
    repo: RepoDep,
    status: str | None = Query(default=None),
    cluster_id: int | None = Query(default=None),
    plant_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: int | None = Query(default=None, description="Id cursor — return alerts with id < cursor"),
) -> AlertListResponse:
    """List persisted alerts from the inbox with optional filters and cursor pagination.

    Walks the inbox newest-first. To fetch the next page, pass the previous
    response's ``next_cursor`` (the last alert id seen) as ``cursor``.

    Args:
        status: Filter by alert lifecycle status (`open`, `acknowledged`, `resolved`).
        cluster_id: Restrict to alerts for a specific cluster.
        plant_id: Restrict to alerts for a specific plant.
        limit: Maximum number of items to return (default 100, max 500).
        cursor: Id-based cursor — return only rows with ``id < cursor``.

    Returns:
        Open-alert badge count, the filtered alert list newest-seen first,
        and a ``next_cursor`` (None when the page was not full).
    """
    items = repo.list_alerts(
        status=status,
        cluster_id=cluster_id,
        plant_id=plant_id,
        limit=limit,
        after_id=cursor,
    )
    open_count = repo.count_open_alerts()
    next_cursor = items[-1].id if len(items) == limit else None
    return AlertListResponse(
        open_count=open_count,
        items=[AlertSummary.model_validate(a) for a in items],
        next_cursor=next_cursor,
    )


@router.get("/alerts/{alert_id}", response_model=AlertSummary)
def get_alert(alert_id: int, repo: RepoDep) -> AlertSummary:
    """Fetch a single alert by ID.

    Args:
        alert_id: Numeric alert identifier.

    Returns:
        The alert row with full lifecycle metadata.

    Raises:
        HTTPException: 404 if no alert with that ID exists.
    """
    alert = repo.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertSummary.model_validate(alert)


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertSummary)
def acknowledge_alert(alert_id: int, repo: RepoDep, session: SessionDep) -> AlertSummary:
    """Move an open alert to the acknowledged state.

    Idempotent: acknowledging an already-acknowledged alert leaves it unchanged.

    Args:
        alert_id: Numeric alert identifier.

    Returns:
        The updated alert row.

    Raises:
        HTTPException: 404 if no alert with that ID exists.
    """
    alert = repo.acknowledge_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    session.commit()
    return AlertSummary.model_validate(alert)


@router.post("/alerts/{alert_id}/resolve", response_model=AlertSummary)
def resolve_alert(alert_id: int, repo: RepoDep, session: SessionDep) -> AlertSummary:
    """Mark an alert as resolved, closing the inbox entry.

    Args:
        alert_id: Numeric alert identifier.

    Returns:
        The updated alert row with `resolved_at` set.

    Raises:
        HTTPException: 404 if no alert with that ID exists.
    """
    alert = repo.resolve_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    session.commit()
    return AlertSummary.model_validate(alert)


@router.post("/clusters/{cluster_id}/alerts/sync", response_model=AlertListResponse)
def refresh_cluster_alerts(
    cluster_id: int,
    repo: RepoDep,
    plant_db: PlantDbDep,
    session: SessionDep,
    notifier: NtfyNotifierDep,
) -> AlertListResponse:
    """Recompute alerts for a single cluster and reconcile the inbox.

    Evaluates learning and maintenance findings for the cluster, upserts new
    alerts, and auto-resolves any previously-open alerts whose triggering
    condition has cleared.

    Args:
        cluster_id: Cluster to sync alerts for.

    Returns:
        Open-alert badge count and the cluster's current alert list.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    alerts = sync_cluster_alerts(repo, cluster_id, plant_db, notifier=notifier)
    open_count = repo.count_open_alerts()
    session.commit()
    return AlertListResponse(open_count=open_count, items=[AlertSummary.model_validate(a) for a in alerts])


@router.post("/alerts/sync", response_model=AlertListResponse)
def refresh_all_alerts(
    repo: RepoDep,
    plant_db: PlantDbDep,
    session: SessionDep,
    notifier: NtfyNotifierDep,
) -> AlertListResponse:
    """Recompute and reconcile alerts across all clusters.

    Runs sync_cluster_alerts for every cluster. Returns the full alert list
    across all clusters after reconciliation.

    Returns:
        Open-alert badge count and the full post-sync alert list.
    """
    sync_all_alerts(repo, plant_db, notifier=notifier)
    items = repo.list_alerts(limit=500)
    open_count = repo.count_open_alerts()
    session.commit()
    return AlertListResponse(open_count=open_count, items=[AlertSummary.model_validate(a) for a in items])
