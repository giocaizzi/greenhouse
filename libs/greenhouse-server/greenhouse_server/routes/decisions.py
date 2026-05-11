"""Decision log routes."""

from fastapi import APIRouter, Query

from greenhouse_core.schemas import DecisionLogListResponse, DecisionLogResponse
from greenhouse_server.deps import RepoDep, require_cluster

router = APIRouter(tags=["decisions"])


@router.get("/clusters/{cluster_id}/decisions", response_model=DecisionLogListResponse)
def list_decisions(
    cluster_id: int,
    repo: RepoDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> DecisionLogListResponse:
    """Return the persisted irrigation decision log for a cluster, newest first.

    Every call to the decision engine with `persist=True` writes a row here,
    whether or not the decision was acted on. Use this to audit "why did you
    skip at 3am?" or replay the reasoning trail in the UI.

    Args:
        cluster_id: Cluster whose decision log to query.
        limit: Maximum number of entries to return (default 50, max 200).

    Returns:
        Cluster ID and ordered decision log entries including action, confidence,
        reason text, and whether the decision was actuated.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    require_cluster(repo, cluster_id)
    rows = repo.list_decision_logs(cluster_id=cluster_id, limit=limit)
    return DecisionLogListResponse(
        cluster_id=cluster_id,
        items=[DecisionLogResponse.model_validate(r) for r in rows],
    )
