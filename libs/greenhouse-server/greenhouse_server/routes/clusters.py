"""Cluster CRUD routes."""

from fastapi import APIRouter, HTTPException, Query, status

from greenhouse_core.schemas import (
    ClusterDetailResponse,
    ClusterResponse,
    ConfigResponse,
    CreateClusterRequest,
    IrrigationWindowResponse,
    IrrigatorResponse,
    PlantResponse,
    SensorResponse,
    SuccessResponse,
    UpdateClusterRequest,
)
from greenhouse_server.deps import RepoDep, require_cluster

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

    Returns:
        The cluster row. Use ``GET /clusters/{cluster_id}/detail`` to inline
        the cluster's plants, sensors, irrigators, config, and windows in a
        single round-trip.

    Raises:
        HTTPException: 404 if no cluster with that ID exists.
    """
    return require_cluster(repo, cluster_id)


@router.get(
    "/{cluster_id}/detail",
    response_model=ClusterDetailResponse,
    summary="Get a cluster with its child resources inlined",
)
def get_cluster_detail(
    cluster_id: int,
    repo: RepoDep,
    expand: str = Query(
        default="children",
        description="Reserved for future expansion levels — currently must be ``children`` (the default).",
    ),
):
    """Return a cluster together with every child resource in one round-trip.

    Inlines the cluster's plants, sensors, irrigators, irrigation config, and
    irrigation windows so a UI or MCP tool can render a full cluster view
    without issuing a fan-out of follow-up requests. The plain
    ``GET /clusters/{cluster_id}`` endpoint remains unchanged for clients that
    only need the cluster row itself.

    Args:
        cluster_id: Numeric cluster identifier.
        expand: Reserved for future expansion variants — currently only
            ``children`` is honored (and is also the default).

    Returns:
        The cluster wrapped together with plants, sensors, irrigators,
        irrigation config, and irrigation windows.

    Raises:
        HTTPException: 404 if no cluster with that ID exists.
    """
    cluster = require_cluster(repo, cluster_id)
    # ``expand`` is accepted for API forward-compatibility but currently has
    # one meaningful value; anything else is treated as ``children``.
    _ = expand
    config = repo.get_irrigation_config(cluster_id)
    return ClusterDetailResponse(
        cluster=ClusterResponse.model_validate(cluster),
        plants=[PlantResponse.model_validate(p) for p in repo.get_plants_in_cluster(cluster_id)],
        sensors=[SensorResponse.model_validate(s) for s in repo.get_sensors_in_cluster(cluster_id)],
        irrigators=[IrrigatorResponse.model_validate(i) for i in repo.get_irrigators_in_cluster(cluster_id)],
        config=ConfigResponse.model_validate(config) if config else None,
        windows=[IrrigationWindowResponse.model_validate(w) for w in repo.list_irrigation_windows(cluster_id)],
    )


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
