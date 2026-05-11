"""FastAPI dependency injection."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from greenhouse_core.cloud import TuyaCloud
from greenhouse_core.devices import TuyaDeviceManager
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.cluster import ClusterService
from greenhouse_server.services.health import PlantHealthService
from greenhouse_server.services.irrigation import IrrigationService
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


def get_device_manager(request: Request) -> TuyaDeviceManager | None:
    return getattr(request.app.state, "device_manager", None)


def get_tuya_cloud() -> TuyaCloud | None:
    try:
        return TuyaCloud()
    except Exception:
        return None


def get_weather_client(request: Request) -> WeatherClient:
    return request.app.state.weather_client


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
    cloud: Annotated[TuyaCloud | None, Depends(get_tuya_cloud)],
) -> SyncService:
    return SyncService(repo, cloud)


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
    dm: Annotated[TuyaDeviceManager | None, Depends(get_device_manager)],
    sync_service: Annotated[SyncService, Depends(get_sync_service)],
    weather: Annotated[WeatherClient, Depends(get_weather_client)],
    plant_db: Annotated[PlantDatabase, Depends(get_plant_db)],
) -> IrrigationService:
    return IrrigationService(repo, dm, sync_service, weather, plant_db)


# --- Type aliases for route injection ---

SessionDep = Annotated[Session, Depends(get_session)]
RepoDep = Annotated[IrrigationRepository, Depends(get_repository)]
DeviceManagerDep = Annotated[TuyaDeviceManager | None, Depends(get_device_manager)]
TuyaCloudDep = Annotated[TuyaCloud | None, Depends(get_tuya_cloud)]
WeatherClientDep = Annotated[WeatherClient, Depends(get_weather_client)]
PlantDbDep = Annotated[PlantDatabase, Depends(get_plant_db)]
SyncServiceDep = Annotated[SyncService, Depends(get_sync_service)]
ClusterServiceDep = Annotated[ClusterService, Depends(get_cluster_service)]
IrrigationServiceDep = Annotated[IrrigationService, Depends(get_irrigation_service)]
PlantHealthServiceDep = Annotated[PlantHealthService, Depends(get_plant_health_service)]
