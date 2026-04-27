"""Irrigation efficacy endpoint: score soil-moisture rise per completed event."""

from fastapi import APIRouter, Query

from tuya_irrigation_core.schemas import EfficacyListResponse
from tuya_irrigation_server.deps import RepoDep, require_cluster
from tuya_irrigation_server.services.efficacy import score_cluster

router = APIRouter(tags=["operations"])


@router.get("/clusters/{cluster_id}/efficacy", response_model=EfficacyListResponse)
def cluster_efficacy(
    cluster_id: int,
    repo: RepoDep,
    days: int = Query(default=14, ge=1),
) -> EfficacyListResponse:
    """Score completed irrigation events by post-irrigation soil-moisture rise.

    For each `start` event with a positive `duration_minutes`, compares the
    average soil moisture across all cluster sensors in the 30 minutes before
    the event against the 90-minute window after. Score = clamp((after - before)
    × 5, 0, 100): a 20 percentage-point rise yields score 100; less than
    0 percentage-point rise yields 0. Score is None when sensor data is
    insufficient around the event.

    Args:
        cluster_id: Cluster to score.
        days: Look-back window in days (default 14).

    Returns:
        EfficacyListResponse with one EfficacyItemResponse per qualifying event,
        ordered newest-first.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    require_cluster(repo, cluster_id)
    return score_cluster(repo, cluster_id, days=days)
