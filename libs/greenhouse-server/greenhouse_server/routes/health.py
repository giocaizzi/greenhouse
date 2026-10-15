"""System health pulse: detailed sensor freshness, device inventory, cloud reachability."""

from fastapi import APIRouter

from greenhouse_core.schemas import SystemHealthResponse
from greenhouse_server.deps import SystemHealthServiceDep

router = APIRouter(tags=["scheduler"])


@router.get("/health/system", response_model=SystemHealthResponse)
def system_health(svc: SystemHealthServiceDep) -> SystemHealthResponse:
    """Return a detailed system health pulse: sensor freshness, irrigator inventory, and cloud reachability.

    A richer sibling of GET /health. Checks every sensor's most recent reading
    timestamp to classify sensors as ok / stale / cold, marks an irrigator
    offline when the device-health monitor holds an offline alarm for it, else
    failing after 3 failed starts in a row (or one while its cluster's
    actuation_failed alert is open; a successful start resets it), infers cloud
    reachability from recent data, counts open alerts, and reports whether the
    background scheduler is running. A failing or offline irrigator makes the
    status degraded.

    Returns:
        SystemHealthResponse with status ("ok" / "degraded" / "down"),
        sensor and irrigator counts, how many irrigators are failing and how
        many offline, open alert count, last sync timestamp, and a per-device
        status list (up to 20 devices, those not ok first, irrigators before
        sensors; an irrigator row carries its consecutive failed starts).
    """
    return svc.pulse()
