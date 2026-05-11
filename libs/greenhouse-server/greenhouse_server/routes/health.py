"""System health pulse: detailed sensor freshness, device inventory, cloud reachability."""

from fastapi import APIRouter

from greenhouse_core.schemas import SystemHealthResponse
from greenhouse_server.deps import RepoDep, SyncServiceDep
from greenhouse_server.services.system_health import SystemHealthService

router = APIRouter(tags=["scheduler"])


@router.get("/health/system", response_model=SystemHealthResponse)
def system_health(repo: RepoDep, sync_svc: SyncServiceDep) -> SystemHealthResponse:
    """Detailed system health pulse: sensor freshness, irrigator inventory, and cloud reachability.

    A richer sibling of GET /health. Checks every sensor's most recent reading
    timestamp to classify devices as ok / stale / cold, infers cloud
    reachability from recent data, counts open alerts, and reports whether the
    background scheduler is running.

    Returns:
        SystemHealthResponse with status ("ok" / "degraded" / "down"),
        sensor and irrigator counts, open alert count, last sync timestamp,
        and a per-device status list (up to 20 devices).
    """
    svc = SystemHealthService(repo, sync_svc)
    return svc.pulse()
