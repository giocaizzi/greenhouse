"""Bulk operation routes."""

from fastapi import APIRouter

from greenhouse_core.schemas import StopAllResponse
from greenhouse_server.deps import DeviceRegistryDep, RepoDep
from greenhouse_server.services.bulk import stop_all_irrigators

router = APIRouter(prefix="/bulk", tags=["bulk"])


@router.post("/stop-all", response_model=StopAllResponse, summary="Emergency stop all irrigators")
def bulk_stop_all(repo: RepoDep, registry: DeviceRegistryDep):
    """Send an emergency stop command to every irrigator in the system.

    Iterates all irrigators regardless of cluster, calls the per-adapter
    stop command for each, and logs an IrrigationEvent with
    ``triggered_by="emergency"`` and ``notes="kill switch"``. When the device
    registry is unavailable the event is still logged and the irrigator is
    counted as stopped. Per-irrigator errors are collected rather than aborting
    the whole batch.

    Returns:
        Count of irrigators successfully stopped and a list of per-device
        error strings for any that raised an exception.
    """
    stopped, errors = stop_all_irrigators(repo, registry)
    return StopAllResponse(stopped=stopped, errors=errors)
