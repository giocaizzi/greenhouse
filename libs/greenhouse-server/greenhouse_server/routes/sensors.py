"""Sensor CRUD routes."""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from greenhouse_core.schemas import (
    CreateSensorRequest,
    SensorAssignmentListResponse,
    SensorAssignmentResponse,
    SensorListResponse,
    SensorResponse,
    SuccessResponse,
    UpdateSensorRequest,
)
from greenhouse_server.deps import RepoDep, require_cluster

router = APIRouter(tags=["sensors"])


@router.get("/sensors", response_model=SensorListResponse, summary="List sensors across all clusters")
def list_all_sensors(
    repo: RepoDep,
    cluster_id: int | None = Query(default=None, description="Restrict results to a specific cluster"),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: int | None = Query(default=None, description="Id cursor — return rows with id > cursor"),
):
    """List every sensor across all clusters with optional cluster filter and cursor pagination.

    Args:
        cluster_id: Restrict to a single cluster.
        limit: Page size (default 100, max 500).
        cursor: Id-based cursor — pass the previous response's ``next_cursor``
            to fetch the next page.

    Returns:
        The page of sensors plus a ``next_cursor`` (None when the page was not
        full and there are no more rows to fetch).
    """
    rows = repo.list_all_sensors(filter_cluster_id=cluster_id, limit=limit, after_id=cursor)
    next_cursor = rows[-1].id if len(rows) == limit else None
    return SensorListResponse(
        sensors=[SensorResponse.model_validate(r) for r in rows],
        next_cursor=next_cursor,
    )


@router.post("/clusters/{cluster_id}/sensors", response_model=SensorResponse, status_code=status.HTTP_201_CREATED)
def add_sensor(cluster_id: int, request: CreateSensorRequest, repo: RepoDep):
    """Register a Tuya sensor under a cluster.

    A sensor may optionally be linked to a specific plant; otherwise it is
    treated as a cluster-level reading.

    Args:
        cluster_id: Cluster the sensor belongs to.
        request: Tuya device ID, sensor name, type (e.g. soil_moisture),
            optional config dict, and optional plant_id for per-plant linking.

    Raises:
        HTTPException: 404 if the cluster (or referenced plant) is missing,
            409 if the Tuya device ID is already registered.

    Returns:
        The created sensor record.
    """
    require_cluster(repo, cluster_id)
    if request.plant_id:
        plants = repo.get_plants_in_cluster(cluster_id)
        if not any(p.id == request.plant_id for p in plants):
            raise HTTPException(status_code=404, detail=f"Plant {request.plant_id} not found in cluster")
    try:
        sensor_id = repo.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=request.tuya_device_id,
            name=request.name,
            sensor_type=request.type,
            config=request.config or {},
            plant_id=request.plant_id,
        )
        repo.session.commit()
    except IntegrityError:
        repo.session.rollback()
        raise HTTPException(status_code=409, detail="Device ID already exists") from None
    sensors = repo.get_sensors_in_cluster(cluster_id)
    return next(s for s in sensors if s.id == sensor_id)


@router.get("/clusters/{cluster_id}/sensors", response_model=list[SensorResponse])
def list_sensors(cluster_id: int, repo: RepoDep):
    """List every sensor registered to a cluster.

    Args:
        cluster_id: ID of the cluster to enumerate.
    """
    return repo.get_sensors_in_cluster(cluster_id)


@router.get("/clusters/{cluster_id}/sensors/{sensor_id}", response_model=SensorResponse, summary="Get a sensor by ID")
def get_sensor(cluster_id: int, sensor_id: int, repo: RepoDep):
    """Fetch a single sensor by ID.

    Args:
        cluster_id: Cluster the sensor belongs to.
        sensor_id: Numeric sensor identifier.

    Returns:
        The sensor record.

    Raises:
        HTTPException: 404 if the sensor does not exist or belongs to a
            different cluster.
    """
    sensor = repo.get_sensor(sensor_id)
    if not sensor or sensor.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="Sensor not found in cluster")
    return sensor


@router.put("/clusters/{cluster_id}/sensors/{sensor_id}", response_model=SensorResponse, summary="Update a sensor")
def update_sensor(cluster_id: int, sensor_id: int, request: UpdateSensorRequest, repo: RepoDep):
    """Partially update a sensor metadata.

    Only fields present in the request body are modified; omitted fields are
    left unchanged. The sensor must belong to the specified cluster.

    Args:
        cluster_id: Cluster the sensor belongs to.
        sensor_id: Numeric sensor identifier.
        request: Fields to update — any subset of name, type, config,
            and plant_id.

    Returns:
        The updated sensor.

    Raises:
        HTTPException: 404 if the sensor does not exist or belongs to a
            different cluster.
    """
    sensor = repo.get_sensor(sensor_id)
    if not sensor or sensor.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="Sensor not found in cluster")
    updated = repo.update_sensor(sensor_id, **request.model_dump(exclude_none=True))
    repo.session.commit()
    return updated


@router.get(
    "/sensors/{sensor_id}/assignments",
    response_model=SensorAssignmentListResponse,
    summary="List the sensor's plant-assignment history",
)
def list_sensor_assignments(sensor_id: int, repo: RepoDep):
    """Return every plant this sensor has ever been linked to, oldest first.

    Each row covers the interval ``[started_at, ended_at)``. ``ended_at=None``
    on the latest row means the sensor is currently assigned. Used by the UI
    to explain why a plant's history changes shape when a sensor is moved,
    and by audit tooling to verify attribution.

    Args:
        sensor_id: Numeric sensor identifier.

    Returns:
        Full assignment list (possibly empty if the sensor was never linked).

    Raises:
        HTTPException: 404 if the sensor does not exist.
    """
    sensor = repo.get_sensor(sensor_id)
    if sensor is None:
        raise HTTPException(status_code=404, detail="Sensor not found")
    rows = repo.list_sensor_assignments(sensor_id)
    return SensorAssignmentListResponse(
        sensor_id=sensor_id,
        assignments=[SensorAssignmentResponse.model_validate(r) for r in rows],
    )


@router.delete("/clusters/{cluster_id}/sensors/{sensor_id}", response_model=SuccessResponse, summary="Delete a sensor")
def delete_sensor(cluster_id: int, sensor_id: int, repo: RepoDep):
    """Delete a sensor and all its historical readings.

    This operation is irreversible. The sensor must belong to the specified
    cluster.

    Args:
        cluster_id: Cluster the sensor belongs to.
        sensor_id: Numeric sensor identifier.

    Returns:
        success=True on successful deletion.

    Raises:
        HTTPException: 404 if the sensor does not exist or belongs to a
            different cluster.
    """
    sensor = repo.get_sensor(sensor_id)
    if not sensor or sensor.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="Sensor not found in cluster")
    repo.delete_sensor(sensor_id)
    repo.session.commit()
    return SuccessResponse(success=True)
