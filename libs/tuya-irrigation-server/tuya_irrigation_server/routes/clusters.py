"""Cluster CRUD routes."""

from fastapi import APIRouter, HTTPException, status

from tuya_irrigation_core.schemas import ClusterResponse, CreateClusterRequest, SuccessResponse, UpdateClusterRequest
from tuya_irrigation_server.deps import RepoDep, require_cluster

router = APIRouter(prefix="/clusters", tags=["clusters"])


@router.post("", response_model=ClusterResponse, status_code=status.HTTP_201_CREATED, summary="Create a cluster")
def create_cluster(request: CreateClusterRequest, repo: RepoDep):
    """Create a new plant cluster.

    A cluster groups plants that share an irrigator and are watered together;
    irrigation decisions are made per-cluster, driven by the driest plant.

    Args:
        request: Cluster name, optional location label, and `environment`
            (`indoor` or `outdoor`; affects how temperature is resolved).

    Returns:
        The newly created cluster including its assigned ID.
    """
    cluster_id = repo.add_cluster(request.name, request.location, request.environment)
    repo.session.commit()
    return repo.get_cluster(cluster_id)


@router.get("", response_model=list[ClusterResponse], summary="List all clusters")
def list_clusters(repo: RepoDep):
    """List every cluster in the system."""
    return repo.list_clusters()


@router.get("/{cluster_id}", response_model=ClusterResponse, summary="Get a cluster by ID")
def get_cluster(cluster_id: int, repo: RepoDep):
    """Fetch a single cluster by ID.

    Args:
        cluster_id: Numeric cluster identifier.

    Raises:
        HTTPException: 404 if no cluster with that ID exists.
    """
    return require_cluster(repo, cluster_id)


@router.put("/{cluster_id}", response_model=ClusterResponse, summary="Update a cluster")
def update_cluster(cluster_id: int, request: UpdateClusterRequest, repo: RepoDep):
    """Partially update a cluster metadata.

    Only fields present in the request body are modified; omitted fields are
    left unchanged.

    Args:
        cluster_id: Numeric cluster identifier.
        request: Fields to update — any combination of name, location, and
            environment.

    Returns:
        The updated cluster.

    Raises:
        HTTPException: 404 if no cluster with that ID exists.
    """
    cluster = repo.update_cluster(cluster_id, **request.model_dump(exclude_none=True))
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    repo.session.commit()
    return cluster


@router.delete("/{cluster_id}", response_model=SuccessResponse, summary="Delete a cluster")
def delete_cluster(cluster_id: int, repo: RepoDep):
    """Delete a cluster and all its associated data.

    Cascades to plants, sensors, irrigators, and irrigation config. This
    operation is irreversible.

    Args:
        cluster_id: Numeric cluster identifier.

    Returns:
        success=True on successful deletion.

    Raises:
        HTTPException: 404 if no cluster with that ID exists.
    """
    deleted = repo.delete_cluster(cluster_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cluster not found")
    repo.session.commit()
    return SuccessResponse(success=True)
