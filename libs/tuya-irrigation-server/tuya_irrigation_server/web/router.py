"""Top-level web router that assembles all sub-routers."""

from fastapi import APIRouter

from tuya_irrigation_server.web.routes import (
    analytics,
    clusters,
    configs,
    fragments,
    irrigators,
    operations,
    pages,
    plant_dashboard,
    plants,
    sensors,
)

web_router = APIRouter(include_in_schema=False)
web_router.include_router(pages.router)
web_router.include_router(fragments.router)
web_router.include_router(clusters.router)
web_router.include_router(plants.router)
web_router.include_router(plant_dashboard.router)
web_router.include_router(sensors.router)
web_router.include_router(irrigators.router)
web_router.include_router(configs.router)
web_router.include_router(operations.router)
web_router.include_router(analytics.router)
