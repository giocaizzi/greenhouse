"""Sensor CRUD routes."""

import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from greenhouse_core.models import Sensor
from greenhouse_core.repository import InvalidMoveTimeError
from greenhouse_core.schemas import (
    CreateSensorRequest,
    MoveSensorRequest,
    SensorAssignmentListResponse,
    SensorAssignmentResponse,
    SensorListResponse,
    SensorResponse,
    SuccessResponse,
    UpdateSensorRequest,
)
from greenhouse_server.deps import (
    DeviceRegistryDep,
    RepoDep,
    require_cluster,
    require_sensor,
    require_sensor_in_cluster,
)
from greenhouse_server.services.inventory import (
    DeviceIdExistsError,
    DeviceSpec,
    PlantNotInClusterError,
    UnknownDeviceTypeError,
    create_sensor,
    ensure_plant_in_cluster,
    keep_masked_local_key,
    require_known_device_type,
)

router = APIRouter(tags=["sensors"])


@router.get("/sensors", response_model=SensorListResponse, summary="List sensors across all clusters")
def list_all_sensors(
    repo: RepoDep,
    cluster_id: Annotated[int | None, Query(description="Restrict results to a specific cluster")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[int | None, Query(description="Id cursor — return rows with id > cursor")] = None,
) -> SensorListResponse:
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
def add_sensor(cluster_id: int, request: CreateSensorRequest, repo: RepoDep, registry: DeviceRegistryDep) -> Sensor:
    """Register a Tuya sensor under a cluster.

    A sensor may optionally be linked to a specific plant; otherwise it is
    treated as a cluster-level reading.

    Args:
        cluster_id: Cluster the sensor belongs to.
        request: Tuya device ID, sensor name, type (the device model key,
            e.g. `tuya.tr301z`),
            optional config dict, and optional plant_id for per-plant linking.

    Raises:
        HTTPException: 404 if the cluster (or referenced plant) is missing,
            409 if the Tuya device ID is already registered, 422 if `type` is
            not a registered sensor model key (the detail lists the known keys).

    Returns:
        The created sensor record.
    """
    require_cluster(repo, cluster_id)
    try:
        sensor_id = create_sensor(
            repo,
            cluster_id,
            DeviceSpec(request.tuya_device_id, request.name, request.type, request.config or {}),
            registry=registry,
            plant_id=request.plant_id,
        )
    except UnknownDeviceTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except PlantNotInClusterError as exc:
        raise HTTPException(status_code=404, detail=f"Plant {exc.plant_id} not found in cluster") from None
    except DeviceIdExistsError:
        raise HTTPException(status_code=409, detail="Device ID already exists") from None
    repo.commit()
    sensors = repo.get_sensors_in_cluster(cluster_id)
    return next(s for s in sensors if s.id == sensor_id)


@router.get("/clusters/{cluster_id}/sensors", response_model=list[SensorResponse])
def list_sensors(cluster_id: int, repo: RepoDep) -> list[Sensor]:
    """List every sensor registered to a cluster.

    Args:
        cluster_id: ID of the cluster to enumerate.

    Returns:
        The cluster's sensors (empty for an unknown cluster).
    """
    return repo.get_sensors_in_cluster(cluster_id)


@router.get("/clusters/{cluster_id}/sensors/{sensor_id}", response_model=SensorResponse, summary="Get a sensor by ID")
def get_sensor(cluster_id: int, sensor_id: int, repo: RepoDep) -> Sensor:
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
    return require_sensor_in_cluster(repo, cluster_id, sensor_id)


@router.put("/clusters/{cluster_id}/sensors/{sensor_id}", response_model=SensorResponse, summary="Update a sensor")
def update_sensor(
    cluster_id: int, sensor_id: int, request: UpdateSensorRequest, repo: RepoDep, registry: DeviceRegistryDep
) -> Sensor | None:
    """Partially update a sensor's metadata.

    Only fields present in the request body are modified; omitted fields are
    left unchanged. An explicit `null` clears an optional field (`plant_id:
    null` unassigns the sensor) and is ignored for a required one;
    `config: null` clears the whole config, `local_key` included. The sensor
    must belong to the specified cluster. A `config.local_key` is masked as
    `••••••••` in every response; sending that mask back keeps the stored key.

    Args:
        cluster_id: Cluster the sensor belongs to.
        sensor_id: Numeric sensor identifier.
        request: Fields to update — any subset of name, type, config,
            and plant_id.

    Returns:
        The updated sensor.

    Raises:
        HTTPException: 404 if the sensor does not exist or belongs to a
            different cluster, or if ``plant_id`` is not a plant of that cluster;
            422 if a changed `type` is not a registered sensor model key.
    """
    sensor = require_sensor_in_cluster(repo, cluster_id, sensor_id)
    try:
        if request.type is not None:
            require_known_device_type(registry, "sensor", request.type, stored=sensor.type)
        ensure_plant_in_cluster(repo, cluster_id, request.plant_id)
    except UnknownDeviceTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except PlantNotInClusterError as exc:
        raise HTTPException(status_code=404, detail=f"Plant {exc.plant_id} not found in cluster") from None
    fields = request.model_dump(exclude_unset=True)
    if fields.get("config"):
        fields["config"] = keep_masked_local_key(fields["config"], sensor.config)
    updated = repo.update_sensor(sensor_id, **fields)
    repo.commit()
    return updated


@router.get(
    "/sensors/{sensor_id}/assignments",
    response_model=SensorAssignmentListResponse,
    summary="List the sensor's plant-assignment history",
)
def list_sensor_assignments(sensor_id: int, repo: RepoDep) -> SensorAssignmentListResponse:
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
    require_sensor(repo, sensor_id)
    rows = repo.list_sensor_assignments(sensor_id)
    return SensorAssignmentListResponse(
        sensor_id=sensor_id,
        assignments=[SensorAssignmentResponse.model_validate(r) for r in rows],
    )


@router.post(
    "/clusters/{cluster_id}/sensors/{sensor_id}/move",
    response_model=SensorAssignmentListResponse,
    summary="Move a sensor to another plant, effective at a given time",
)
def move_sensor(
    cluster_id: int, sensor_id: int, request: MoveSensorRequest, repo: RepoDep
) -> SensorAssignmentListResponse:
    """Record that a sensor was physically moved to another plant (or detached), and when.

    Readings are attributed to a plant by the sensor's assignment history, so
    dating the move right matters: readings before `effective_at` stay with the
    old plant, readings after it (past a settle window, 30 min by default, while
    the probe is not judged) count for the new one. Sending the plant the sensor
    is already on re-dates its last move instead — e.g. a move done at 09:00 but
    entered at 14:00. Writes a `sensor_reassigned` activity event.

    Args:
        cluster_id: Cluster the sensor belongs to.
        sensor_id: Numeric sensor identifier.
        request: `plant_id` (null detaches) and optional `effective_at` (Unix
            seconds, default now).

    Returns:
        The sensor's assignment history after the move, oldest first.

    Raises:
        HTTPException: 404 if the sensor does not exist or belongs to a
            different cluster, or if `plant_id` is not a plant of that cluster;
            400 if `effective_at` is in the future, before the current
            assignment started, or would overlap the previous assignment.
    """
    require_sensor_in_cluster(repo, cluster_id, sensor_id)
    try:
        ensure_plant_in_cluster(repo, cluster_id, request.plant_id)
    except PlantNotInClusterError as exc:
        raise HTTPException(status_code=404, detail=f"Plant {exc.plant_id} not found in cluster") from None
    now = int(time.time())
    when = request.effective_at if request.effective_at is not None else now
    if when > now:
        raise HTTPException(status_code=400, detail="effective_at: must not be in the future")
    try:
        repo.move_sensor(sensor_id, request.plant_id, when=when)
    except InvalidMoveTimeError as exc:
        raise HTTPException(status_code=400, detail=f"effective_at: {exc}") from None
    repo.commit()
    rows = repo.list_sensor_assignments(sensor_id)
    return SensorAssignmentListResponse(
        sensor_id=sensor_id,
        assignments=[SensorAssignmentResponse.model_validate(r) for r in rows],
    )


@router.delete("/clusters/{cluster_id}/sensors/{sensor_id}", response_model=SuccessResponse, summary="Delete a sensor")
def delete_sensor(cluster_id: int, sensor_id: int, repo: RepoDep) -> SuccessResponse:
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
    require_sensor_in_cluster(repo, cluster_id, sensor_id)
    repo.delete_sensor(sensor_id)
    repo.commit()
    return SuccessResponse(success=True)
