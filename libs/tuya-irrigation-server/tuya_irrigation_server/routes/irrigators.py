"""Irrigator CRUD + control routes."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from tuya_irrigation_core.schemas import (
    CreateIrrigatorRequest,
    IrrigatorActionResponse,
    IrrigatorResponse,
    LogManualRequest,
    LogManualResponse,
    StartIrrigatorRequest,
)
from tuya_irrigation_server.deps import DeviceManagerDep, RepoDep, require_cluster

router = APIRouter(tags=["irrigators"])


@router.post("/clusters/{cluster_id}/irrigators", response_model=IrrigatorResponse, status_code=status.HTTP_201_CREATED)
def add_irrigator(cluster_id: int, request: CreateIrrigatorRequest, repo: RepoDep):
    """Register a Tuya irrigator under a cluster.

    Args:
        cluster_id: Cluster the irrigator belongs to.
        request: Tuya device ID, irrigator name, type (e.g. `tuya_cloud`),
            and optional config dict.

    Raises:
        HTTPException: 404 if the cluster does not exist, 409 if the Tuya
            device ID is already registered.
    """
    require_cluster(repo, cluster_id)
    try:
        irrigator_id = repo.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=request.tuya_device_id,
            name=request.name,
            irrigator_type=request.type,
            config=request.config or {},
        )
        repo.session.commit()
    except IntegrityError:
        repo.session.rollback()
        raise HTTPException(status_code=409, detail="Device ID already exists") from None
    return repo.get_irrigator(irrigator_id)


@router.get("/clusters/{cluster_id}/irrigators", response_model=list[IrrigatorResponse])
def list_irrigators(cluster_id: int, repo: RepoDep):
    """List every irrigator registered to a cluster.

    Args:
        cluster_id: ID of the cluster to enumerate.
    """
    return repo.get_irrigators_in_cluster(cluster_id)


@router.post("/irrigators/{irrigator_id}/start", response_model=IrrigatorActionResponse)
def start_irrigator(
    irrigator_id: int,
    request: StartIrrigatorRequest,
    repo: RepoDep,
    dm: DeviceManagerDep,
) -> IrrigatorActionResponse:
    """Manually start an irrigator over the Tuya local protocol.

    Side effects: actuates physical hardware and records a `start` irrigation
    event with `triggered_by="manual"`. Bypasses the smart-decision engine
    and the global cooldown.

    Args:
        irrigator_id: Irrigator to actuate.
        request: Optional `minutes` for run duration; some irrigator firmware
            ignores duration and runs until explicitly stopped.

    Raises:
        HTTPException: 404 if the irrigator is unknown, 503 if Tuya
            credentials are missing, 502 if the device fails to start.
    """
    irrigator = repo.get_irrigator(irrigator_id)
    if not irrigator:
        raise HTTPException(status_code=404, detail="Irrigator not found")
    if dm is None:
        raise HTTPException(status_code=503, detail="No device manager (missing Tuya credentials)")

    success, output = dm.irrigator_start(irrigator, request.minutes)
    if success:
        repo.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="start",
            duration_minutes=request.minutes,
            triggered_by="manual",
            notes=f"Manual start via API ({request.minutes} min)" if request.minutes else "Manual start via API",
        )
        repo.session.commit()
        return IrrigatorActionResponse(success=True, message=output)
    raise HTTPException(status_code=502, detail=output)


@router.post("/irrigators/{irrigator_id}/stop", response_model=IrrigatorActionResponse)
def stop_irrigator(irrigator_id: int, repo: RepoDep, dm: DeviceManagerDep) -> IrrigatorActionResponse:
    """Manually stop a running irrigator over the Tuya local protocol.

    Side effects: actuates physical hardware and records an `off` irrigation
    event with `triggered_by="manual"`.

    Args:
        irrigator_id: Irrigator to stop.

    Raises:
        HTTPException: 404 if the irrigator is unknown, 503 if Tuya
            credentials are missing, 502 if the device fails to stop.
    """
    irrigator = repo.get_irrigator(irrigator_id)
    if not irrigator:
        raise HTTPException(status_code=404, detail="Irrigator not found")
    if dm is None:
        raise HTTPException(status_code=503, detail="No device manager (missing Tuya credentials)")

    success, output = dm.irrigator_off(irrigator)
    if success:
        repo.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="off",
            triggered_by="manual",
            notes="Manual stop via API",
        )
        repo.session.commit()
        return IrrigatorActionResponse(success=True, message=output)
    raise HTTPException(status_code=502, detail=output)


@router.post("/irrigators/{irrigator_id}/log-manual", response_model=LogManualResponse)
def log_manual(irrigator_id: int, request: LogManualRequest, repo: RepoDep) -> LogManualResponse:
    """Record a manually executed irrigation that did not go through the API.

    Useful when the user watered by hand but wants the learning engine and
    history to know about it. No hardware is actuated.

    Args:
        irrigator_id: Irrigator the manual run is attributed to.
        request: Duration in minutes plus optional notes.

    Raises:
        HTTPException: 404 if the irrigator is unknown.
    """
    irrigator = repo.get_irrigator(irrigator_id)
    if not irrigator:
        raise HTTPException(status_code=404, detail="Irrigator not found")
    event_id = repo.add_irrigation_event(
        irrigator_id=irrigator.id,
        action="start",
        duration_minutes=request.minutes,
        triggered_by="manual",
        notes=request.notes or f"Manual ({request.minutes} min)",
    )
    repo.session.commit()
    return LogManualResponse(success=True, event_id=event_id)
