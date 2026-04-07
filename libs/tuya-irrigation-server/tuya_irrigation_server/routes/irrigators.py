"""Irrigator CRUD + control routes."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from tuya_irrigation_core.schemas import (
    CreateIrrigatorRequest,
    IrrigatorResponse,
    LogManualRequest,
    StartIrrigatorRequest,
)
from tuya_irrigation_server.deps import DeviceManagerDep, RepoDep, require_cluster

router = APIRouter(tags=["irrigators"])


@router.post("/clusters/{cluster_id}/irrigators", response_model=IrrigatorResponse, status_code=status.HTTP_201_CREATED)
def add_irrigator(cluster_id: int, request: CreateIrrigatorRequest, repo: RepoDep):
    require_cluster(repo, cluster_id)
    try:
        irrigator_id = repo.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=request.tuya_device_id,
            name=request.name,
            irrigator_type=request.type,
            config={"config": request.config} if request.config else {},
        )
        repo.session.commit()
    except IntegrityError:
        repo.session.rollback()
        raise HTTPException(status_code=409, detail="Device ID already exists") from None
    return repo.get_irrigator(irrigator_id)


@router.get("/clusters/{cluster_id}/irrigators", response_model=list[IrrigatorResponse])
def list_irrigators(cluster_id: int, repo: RepoDep):
    return repo.get_irrigators_in_cluster(cluster_id)


@router.post("/irrigators/{irrigator_id}/start")
def start_irrigator(irrigator_id: int, request: StartIrrigatorRequest, repo: RepoDep, dm: DeviceManagerDep):
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
        return {"success": True, "message": output}
    raise HTTPException(status_code=502, detail=output)


@router.post("/irrigators/{irrigator_id}/stop")
def stop_irrigator(irrigator_id: int, repo: RepoDep, dm: DeviceManagerDep):
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
        return {"success": True, "message": output}
    raise HTTPException(status_code=502, detail=output)


@router.post("/irrigators/{irrigator_id}/log-manual")
def log_manual(irrigator_id: int, request: LogManualRequest, repo: RepoDep):
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
    return {"success": True, "event_id": event_id}
