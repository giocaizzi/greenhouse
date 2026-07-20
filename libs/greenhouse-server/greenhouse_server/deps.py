"""FastAPI dependency injection."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from greenhouse_core.devices import DeviceGateway, DeviceRegistry
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.cluster import ClusterService
from greenhouse_server.services.health import PlantHealthService
from greenhouse_server.services.health_monitor import DeviceHealthMonitor
from greenhouse_server.services.irrigation import IrrigationService
from greenhouse_server.services.notify import NtfyClient
from greenhouse_server.services.sync import SyncService
from greenhouse_server.services.weather import WeatherClient

# --- Infrastructure dependencies ---


def get_session(request: Request) -> Generator[Session, None, None]:
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_repository(session: Annotated[Session, Depends(get_session)]) -> IrrigationRepository:
    return IrrigationRepository(session)


def get_device_registry(request: Request) -> DeviceRegistry | None:
    return getattr(request.app.state, "device_registry", None)


def get_health_monitor(request: Request) -> DeviceHealthMonitor | None:
    """Return the per-app device-health monitor, if wired.

    The monitor is instantiated in :mod:`greenhouse_server.scheduler` and
    stashed on ``app.state.health_monitor``; tests that don't need the
    health gate leave it unset and the irrigation service falls open.
    The monitor is constructed per-request because it caches state in
    process memory tied to the active session.
    """
    factory: DeviceHealthMonitor | None = getattr(request.app.state, "health_monitor", None)
    return factory


def get_device_gateway(request: Request) -> DeviceGateway | None:
    """Return the one app-scoped Tuya gateway, or ``None`` in degraded mode.

    Built once at startup (``app.state.device_gateway``) so every request
    borrows the single Cloud client and its token — never constructs a new
    one. ``None`` when credentials were absent at startup.
    """
    return getattr(request.app.state, "device_gateway", None)


def get_weather_client(request: Request) -> WeatherClient:
    return request.app.state.weather_client


def get_ntfy_notifier(request: Request) -> NtfyClient | None:
    """Return the ntfy client, or ``None`` when notifications are unconfigured."""
    return getattr(request.app.state, "ntfy_notifier", None)


def get_plant_db(request: Request) -> PlantDatabase:
    return request.app.state.plant_db


# --- Helpers ---


def require_cluster(repo: IrrigationRepository, cluster_id: int):
    """Fetch a cluster or raise 404."""
    from greenhouse_core.models import Cluster

    cluster: Cluster | None = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return cluster


# --- Service dependencies ---


def get_sync_service(
    repo: Annotated[IrrigationRepository, Depends(get_repository)],
    registry: Annotated[DeviceRegistry | None, Depends(get_device_registry)],
    gateway: Annotated[DeviceGateway | None, Depends(get_device_gateway)],
) -> SyncService:
    return SyncService(repo, registry, gateway)


def get_cluster_service(
    repo: Annotated[IrrigationRepository, Depends(get_repository)],
    plant_db: Annotated[PlantDatabase, Depends(get_plant_db)],
) -> ClusterService:
    return ClusterService(repo, plant_db)


def get_plant_health_service(
    repo: Annotated[IrrigationRepository, Depends(get_repository)],
    plant_db: Annotated[PlantDatabase, Depends(get_plant_db)],
) -> PlantHealthService:
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
