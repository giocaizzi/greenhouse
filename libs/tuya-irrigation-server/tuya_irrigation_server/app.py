"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine

from tuya_irrigation_core.database import create_db_engine, create_session_factory, init_db
from tuya_irrigation_core.devices import TuyaDeviceManager
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_server.config import Settings
from tuya_irrigation_server.routes import charts, clusters, configs, irrigators, operations, plants, scheduler, sensors
from tuya_irrigation_server.scheduler import init_scheduler
from tuya_irrigation_server.scheduler import scheduler as bg_scheduler
from tuya_irrigation_server.services.weather import WeatherClient
from tuya_irrigation_server.web.router import web_router


def _init_device_manager() -> TuyaDeviceManager | None:
    """Initialize device manager, returning None if credentials are missing."""
    try:
        return TuyaDeviceManager()
    except (ValueError, Exception):
        return None


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if settings is None:
        settings = Settings()

    if engine is None:
        engine = create_db_engine(settings.db_url)
    init_db(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.enable_scheduler:
            bg_scheduler.start()
        yield
        if settings.enable_scheduler and bg_scheduler.running:
            bg_scheduler.shutdown(wait=False)

    app = FastAPI(
        title="Tuya Irrigation API",
        description=(
            "Smart plant irrigation system with evidence-based plant care, "
            "multi-sensor conflict resolution, and self-learning irrigation profiles.\n\n"
        ),
        version="1.0.0",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "clusters", "description": "Manage plant clusters (groups irrigated together)"},
            {"name": "plants", "description": "Manage plants within clusters"},
            {"name": "irrigators", "description": "Manage and control irrigation devices"},
            {"name": "sensors", "description": "Manage sensor devices"},
            {"name": "configs", "description": "Irrigation configuration per cluster"},
            {"name": "operations", "description": "Smart irrigation, monitoring, sync, stats, and analytics"},
            {"name": "scheduler", "description": "Background job management and health checks"},
        ],
    )

    # Store dependencies on app.state (accessed by deps.py)
    app.state.session_factory = create_session_factory(engine)
    app.state.device_manager = _init_device_manager()
    app.state.weather_client = WeatherClient(lat=settings.weather_lat, lon=settings.weather_lon)
    app.state.plant_db = _init_plant_db(settings)

    init_scheduler(app, settings)

    # Register routes
    prefix = "/api/v1"
    app.include_router(clusters.router, prefix=prefix)
    app.include_router(plants.router, prefix=prefix)
    app.include_router(irrigators.router, prefix=prefix)
    app.include_router(sensors.router, prefix=prefix)
    app.include_router(configs.router, prefix=prefix)
    app.include_router(operations.router, prefix=prefix)
    app.include_router(scheduler.router, prefix=prefix)
    app.include_router(charts.router, prefix=prefix)

    # Web frontend
    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(web_router)

    return app


def _init_plant_db(settings: Settings) -> PlantDatabase:
    """Initialize plant database from settings or default."""
    if settings.plant_db_path:
        from pathlib import Path

        return PlantDatabase(db_path=Path(settings.plant_db_path))
    return PlantDatabase()


def main():
    """Entry point for tuya-irrigation-server command."""
    import uvicorn

    settings = Settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
