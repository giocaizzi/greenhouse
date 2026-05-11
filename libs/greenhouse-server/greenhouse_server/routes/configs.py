"""Irrigation config routes."""

from fastapi import APIRouter, HTTPException

from greenhouse_core.schemas import ConfigResponse, SetConfigRequest
from greenhouse_server.deps import RepoDep, require_cluster

router = APIRouter(tags=["configs"])


@router.put("/clusters/{cluster_id}/config", response_model=ConfigResponse)
def set_config(cluster_id: int, request: SetConfigRequest, repo: RepoDep):
    """Set or replace the irrigation config for a cluster.

    Args:
        cluster_id: Cluster to configure.
        request: Mode (`smart` for the decision engine, `manual` for a fixed
            schedule, `off` to disable), optional `duration_minutes` and
            `interval_hours` for manual mode, and `auto_run` (false disables
            scheduled runs but leaves manual triggers working).

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    require_cluster(repo, cluster_id)
    repo.set_irrigation_config(
        cluster_id=cluster_id,
        mode=request.mode,
        duration_minutes=request.duration_minutes,
        interval_hours=request.interval_hours,
        auto_run=request.auto_run,
    )
    repo.session.commit()
    return repo.get_irrigation_config(cluster_id)


@router.get("/clusters/{cluster_id}/config", response_model=ConfigResponse)
def get_config(cluster_id: int, repo: RepoDep):
    """Read the current irrigation config for a cluster.

    Args:
        cluster_id: Cluster to inspect.

    Raises:
        HTTPException: 404 if the cluster has no config yet.
    """
    config = repo.get_irrigation_config(cluster_id)
    if not config:
        raise HTTPException(status_code=404, detail="No config set for cluster")
    return config
