"""Bulk operation routes."""

from fastapi import APIRouter

from greenhouse_core.schemas import StopAllResponse
from greenhouse_server.deps import DeviceRegistryDep, NtfyNotifierDep, RepoDep
from greenhouse_server.services.bulk import stop_all_irrigators
from greenhouse_server.services.notify import maybe_notify

router = APIRouter(prefix="/bulk", tags=["bulk"])


@router.post("/stop-all", response_model=StopAllResponse, summary="Emergency stop all irrigators")
def bulk_stop_all(repo: RepoDep, registry: DeviceRegistryDep, notifier: NtfyNotifierDep):
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
    maybe_notify(
        notifier,
        repo.get_preferences(),
        "emergency",
        lambda: notifier.notify_irrigation(
            triggered_by="emergency",
            irrigator_name=f"{stopped} irrigator(s)",
            detail="kill switch" + (f", {len(errors)} error(s)" if errors else ""),
        ),
    )
    return StopAllResponse(stopped=stopped, errors=errors)
