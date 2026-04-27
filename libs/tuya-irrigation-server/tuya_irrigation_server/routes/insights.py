"""Cluster care insights aggregated from learning, maintenance, and decisions."""

from fastapi import APIRouter, HTTPException

from tuya_irrigation_core.schemas import ClusterInsightsResponse
from tuya_irrigation_server.deps import PlantDbDep, RepoDep
from tuya_irrigation_server.services.insights import InsightsService

router = APIRouter(tags=["operations"])


@router.get("/clusters/{cluster_id}/insights", response_model=ClusterInsightsResponse)
def cluster_insights(cluster_id: int, repo: RepoDep, plant_db: PlantDbDep) -> ClusterInsightsResponse:
    """Return actionable care insights for a cluster.

    Aggregates stale-data warnings, battery alerts, humidity and light issues
    from the maintenance layer, chronic underwatering and absorption anomalies
    from the learning layer, and a summary of the most recent decision-engine
    evaluation.

    Args:
        cluster_id: Cluster to analyse.

    Returns:
        List of CareInsight items ordered by discovery (maintenance first, then
        learning, then last decision), with severity, a short title, the raw
        alert message, and an imperative suggestion.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    svc = InsightsService(repo, plant_db)
    result = svc.cluster_insights(cluster_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return result
