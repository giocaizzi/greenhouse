"""Forecast route: next-irrigation prediction per cluster."""

from fastapi import APIRouter

from greenhouse_core.schemas import ForecastResponse
from greenhouse_server.deps import ForecastServiceDep, RepoDep, require_cluster

router = APIRouter(tags=["operations"])


@router.get("/clusters/{cluster_id}/forecast", response_model=ForecastResponse)
def get_cluster_forecast(cluster_id: int, repo: RepoDep, forecast_svc: ForecastServiceDep) -> ForecastResponse:
    """Predict when a cluster will next need irrigation.

    Uses per-plant drainage profiles learned from historical irrigation events
    to project the time until the driest plant's soil moisture crosses its
    target-minimum. Falls back to a constant -2.0 %/h rate when no learned
    profile is available. For outdoor clusters, fetches the next-6h precipitation
    forecast and sets `weather_skip=True` when significant rain (>2mm) is expected.

    Args:
        cluster_id: Cluster to forecast.

    Returns:
        Forecast including `next_predicted_at` (Unix timestamp), `hours_until_next`,
        `method` (`drainage_slope` or `fallback_constant`), `confidence` (0–1),
        a human-readable `explanation`, and optional weather context
        (`precipitation_next_6h_mm`, `weather_skip`, `weather_reason`).
        `next_predicted_at` and `hours_until_next` are `null` when no sensor
        data is available.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    require_cluster(repo, cluster_id)
    return forecast_svc.predict_next_irrigation(cluster_id)
