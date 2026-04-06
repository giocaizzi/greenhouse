"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.engine import Engine

from tuya_irrigation_core.database import create_db_engine, create_session_factory, init_db
from tuya_irrigation_core.plant_db import PlantDatabase, set_plant_database
from tuya_irrigation_server.config import Settings
from tuya_irrigation_server.deps import set_session_factory
from tuya_irrigation_server.routes import clusters, configs, irrigators, operations, plants, scheduler, sensors
from tuya_irrigation_server.scheduler import init_scheduler
from tuya_irrigation_server.scheduler import scheduler as bg_scheduler


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if settings is None:
        settings = Settings()

    if engine is None:
        engine = create_db_engine(settings.db_url)
    init_db(engine)
    session_factory = create_session_factory(engine)
    set_session_factory(session_factory)

    # Configure plant database path if set
    if settings.plant_db_path:
        from pathlib import Path

        set_plant_database(PlantDatabase(db_path=Path(settings.plant_db_path)))

    init_scheduler(engine, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bg_scheduler.start()
        yield
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

    # Register routes
    prefix = "/api/v1"
    app.include_router(clusters.router, prefix=prefix)
    app.include_router(plants.router, prefix=prefix)
    app.include_router(irrigators.router, prefix=prefix)
    app.include_router(sensors.router, prefix=prefix)
    app.include_router(configs.router, prefix=prefix)
    app.include_router(operations.router, prefix=prefix)
    app.include_router(scheduler.router, prefix=prefix)

    return app


def main():
    """Entry point for tuya-irrigation-server command."""
    import uvicorn

    settings = Settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
