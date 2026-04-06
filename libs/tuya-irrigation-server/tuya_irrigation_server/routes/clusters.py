"""Cluster CRUD routes."""

from fastapi import APIRouter, HTTPException, status

from tuya_irrigation_core.schemas import ClusterResponse, CreateClusterRequest
from tuya_irrigation_server.deps import RepoDep

router = APIRouter(prefix="/clusters", tags=["clusters"])


@router.post("", response_model=ClusterResponse, status_code=status.HTTP_201_CREATED, summary="Create a cluster")
def create_cluster(request: CreateClusterRequest, repo: RepoDep):
    cluster_id = repo.add_cluster(request.name, request.location, request.environment)
    repo.session.commit()
    return repo.get_cluster(cluster_id)


@router.get("", response_model=list[ClusterResponse], summary="List all clusters")
def list_clusters(repo: RepoDep):
    return repo.list_clusters()


@router.get("/{cluster_id}", response_model=ClusterResponse, summary="Get a cluster by ID")
def get_cluster(cluster_id: int, repo: RepoDep):
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return cluster
