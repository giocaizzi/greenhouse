"""Irrigation config routes."""

from fastapi import APIRouter, HTTPException

from tuya_irrigation_core.schemas import ConfigResponse, SetConfigRequest
from tuya_irrigation_server.deps import RepoDep

router = APIRouter(tags=["configs"])


@router.put("/clusters/{cluster_id}/config", response_model=ConfigResponse)
def set_config(cluster_id: int, request: SetConfigRequest, repo: RepoDep):
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
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
    config = repo.get_irrigation_config(cluster_id)
    if not config:
        raise HTTPException(status_code=404, detail="No config set for cluster")
    return config
