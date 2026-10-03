"""FastAPI dependency injection."""

from collections.abc import Generator
from typing import Annotated, cast, get_args

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from greenhouse_core.devices import DeviceGateway, DeviceRegistry
from greenhouse_core.models import Alert, Cluster, IrrigationWindow, Irrigator, Plant, Sensor, VacationWindow
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server import state
from greenhouse_server.services.charts import Metric
from greenhouse_server.services.cluster import ClusterService
from greenhouse_server.services.health import PlantHealthService
from greenhouse_server.services.health_monitor import DeviceHealthMonitor
from greenhouse_server.services.irrigation import IrrigationService
from greenhouse_server.services.notify import NtfyClient
from greenhouse_server.services.sync import SyncService
from greenhouse_server.services.weather import WeatherClient

# --- Infrastructure dependencies ---


def get_session(request: Request) -> Generator[Session, None, None]:
    """Yield a request-scoped SQLAlchemy session; FastAPI caches it, so every dependency shares it."""
    session = state.session_factory(request.app)()
    try:
        yield session
    finally:
        session.close()


def get_repository(session: Annotated[Session, Depends(get_session)]) -> IrrigationRepository:
    """Return the repository bound to the request's session."""
    return IrrigationRepository(session)


def get_device_registry(request: Request) -> DeviceRegistry | None:
    """Return the app's device registry, or ``None`` when Tuya credentials were missing."""
    return state.device_registry(request.app)


def get_health_monitor(request: Request) -> DeviceHealthMonitor | None:
    """Return the app's device-health monitor singleton, if wired.

    The monitor is instantiated once in :mod:`greenhouse_server.scheduler` and
    stashed on ``app.state.health_monitor``; tests that don't need the
    health gate leave it unset and the irrigation service falls open.
    """
    return state.health_monitor(request.app)


def get_device_gateway(request: Request) -> DeviceGateway | None:
    """Return the one app-scoped Tuya gateway, or ``None`` in degraded mode.

    Built once at startup (``app.state.device_gateway``) so every request
    borrows the single Cloud client and its token — never constructs a new
    one. ``None`` when credentials were absent at startup.
    """
    return state.device_gateway(request.app)


def get_weather_client(request: Request) -> WeatherClient:
    """Return the app-scoped weather client."""
    return state.weather_client(request.app)


def get_ntfy_notifier(request: Request) -> NtfyClient | None:
    """Return the ntfy client, or ``None`` when notifications are unconfigured."""
    return state.ntfy_notifier(request.app)


def get_plant_db(request: Request) -> PlantDatabase:
    """Return the app-scoped plant care database."""
    return state.plant_db(request.app)


# --- Entity lookups (404) ---
# One helper and one detail string per entity, shared by the JSON API and the web UI
# (the web's HTML error page shows the same detail). A "… in cluster" lookup also
# 404s when the row exists but belongs to another cluster.


def require_cluster(repo: IrrigationRepository, cluster_id: int) -> Cluster:
    """Fetch a cluster or raise 404 "Cluster not found"."""
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return cluster


def require_cluster_irrigator(repo: IrrigationRepository, cluster_id: int) -> Irrigator:
    """Fetch the cluster's irrigator or raise 404 "Cluster has no irrigator"."""
    irrigator = repo.get_irrigator_for_cluster(cluster_id)
    if not irrigator:
        raise HTTPException(status_code=404, detail="Cluster has no irrigator")
    return irrigator


def require_irrigator(repo: IrrigationRepository, irrigator_id: int) -> Irrigator:
    """Fetch an irrigator by id or raise 404 "Irrigator not found"."""
    irrigator = repo.get_irrigator(irrigator_id)
    if not irrigator:
        raise HTTPException(status_code=404, detail="Irrigator not found")
    return irrigator


def require_plant(repo: IrrigationRepository, plant_id: int) -> Plant:
    """Fetch a plant by id or raise 404 "Plant not found"."""
    plant = repo.get_plant(plant_id)
    if not plant:
        raise HTTPException(status_code=404, detail="Plant not found")
    return plant


def require_plant_in_cluster(repo: IrrigationRepository, cluster_id: int, plant_id: int) -> Plant:
    """Fetch one of the cluster's plants or raise 404 "Plant not found in cluster"."""
    plant = repo.get_plant(plant_id)
    if not plant or plant.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="Plant not found in cluster")
    return plant


def require_sensor(repo: IrrigationRepository, sensor_id: int) -> Sensor:
    """Fetch a sensor by id or raise 404 "Sensor not found"."""
    sensor = repo.get_sensor(sensor_id)
    if sensor is None:
        raise HTTPException(status_code=404, detail="Sensor not found")
    return sensor


def require_sensor_in_cluster(repo: IrrigationRepository, cluster_id: int, sensor_id: int) -> Sensor:
    """Fetch one of the cluster's sensors or raise 404 "Sensor not found in cluster"."""
    sensor = repo.get_sensor(sensor_id)
    if not sensor or sensor.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="Sensor not found in cluster")
    return sensor


def require_window_in_cluster(repo: IrrigationRepository, cluster_id: int, window_id: int) -> IrrigationWindow:
    """Fetch one of the cluster's irrigation windows or raise 404 "Window not found in cluster"."""
    window = repo.get_irrigation_window(window_id)
    if window is None or window.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="Window not found in cluster")
    return window


def require_vacation_window(repo: IrrigationRepository, window_id: int) -> VacationWindow:
    """Fetch a vacation window or raise 404 "Vacation window not found"."""
    window = repo.get_vacation_window(window_id)
    if not window:
        raise HTTPException(status_code=404, detail="Vacation window not found")
    return window


def require_alert(repo: IrrigationRepository, alert_id: int) -> Alert:
    """Fetch an alert or raise 404 "Alert not found"."""
    alert = repo.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


# --- Query-value validation (400) ---

MAX_LOOKBACK_HOURS = 8760  # one year: upper bound of every chart ``hours`` look-back (API and web)

_CHART_METRICS: frozenset[str] = frozenset(get_args(Metric))


def require_metric(metric: str) -> Metric:
    """Narrow a chart ``metric`` query value to :data:`Metric` or raise 400 "Unsupported metric: …".

    The route parameter stays a plain ``str`` (its OpenAPI schema is frozen); this is the one
    place that turns it into the typed value the chart builders take.
    """
    if metric not in _CHART_METRICS:
        raise HTTPException(400, f"Unsupported metric: {metric}")
    return cast("Metric", metric)


# --- Service dependencies ---


def get_sync_service(
    repo: Annotated[IrrigationRepository, Depends(get_repository)],
    registry: Annotated[DeviceRegistry | None, Depends(get_device_registry)],
    gateway: Annotated[DeviceGateway | None, Depends(get_device_gateway)],
) -> SyncService:
    """Build the sensor sync service on the request's repository."""
    return SyncService(repo, registry, gateway)


def get_cluster_service(
    repo: Annotated[IrrigationRepository, Depends(get_repository)],
    plant_db: Annotated[PlantDatabase, Depends(get_plant_db)],
) -> ClusterService:
    """Build the cluster read service on the request's repository."""
    return ClusterService(repo, plant_db)


def get_plant_health_service(
    repo: Annotated[IrrigationRepository, Depends(get_repository)],
    plant_db: Annotated[PlantDatabase, Depends(get_plant_db)],
) -> PlantHealthService:
    """Build the plant health scoring service on the request's repository."""
    return PlantHealthService(repo, plant_db)


def get_irrigation_service(
    repo: Annotated[IrrigationRepository, Depends(get_repository)],
    registry: Annotated[DeviceRegistry | None, Depends(get_device_registry)],
    sync_service: Annotated[SyncService, Depends(get_sync_service)],
    weather: Annotated[WeatherClient, Depends(get_weather_client)],
    plant_db: Annotated[PlantDatabase, Depends(get_plant_db)],
    health_monitor: Annotated[DeviceHealthMonitor | None, Depends(get_health_monitor)],
    notifier: Annotated[NtfyClient | None, Depends(get_ntfy_notifier)],
) -> IrrigationService:
    """Build the irrigation pipeline service with every collaborator it needs."""
    return IrrigationService(
        repo,
        registry,
        sync_service,
        weather,
        plant_db,
        health_monitor=health_monitor,
        notifier=notifier,
    )


# --- Type aliases for route injection ---

SessionDep = Annotated[Session, Depends(get_session)]
RepoDep = Annotated[IrrigationRepository, Depends(get_repository)]
DeviceRegistryDep = Annotated[DeviceRegistry | None, Depends(get_device_registry)]
DeviceGatewayDep = Annotated[DeviceGateway | None, Depends(get_device_gateway)]
WeatherClientDep = Annotated[WeatherClient, Depends(get_weather_client)]
NtfyNotifierDep = Annotated[NtfyClient | None, Depends(get_ntfy_notifier)]
PlantDbDep = Annotated[PlantDatabase, Depends(get_plant_db)]
SyncServiceDep = Annotated[SyncService, Depends(get_sync_service)]
ClusterServiceDep = Annotated[ClusterService, Depends(get_cluster_service)]
IrrigationServiceDep = Annotated[IrrigationService, Depends(get_irrigation_service)]
PlantHealthServiceDep = Annotated[PlantHealthService, Depends(get_plant_health_service)]
