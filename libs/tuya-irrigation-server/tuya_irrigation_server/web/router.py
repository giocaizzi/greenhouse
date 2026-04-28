"""Top-level web router that assembles all sub-routers."""

from fastapi import APIRouter

from tuya_irrigation_server.web.routes import (
    activity,
    alerts,
    analytics,
    clusters,
    configs,
    fragments,
    health_page,
    irrigators,
    operations,
    pages,
    plant_dashboard,
    plants,
    quality,
    sensors,
    vacation,
)

web_router = APIRouter(include_in_schema=False)
web_router.include_router(activity.router)
web_router.include_router(alerts.router)
web_router.include_router(analytics.router)
web_router.include_router(clusters.router)
web_router.include_router(configs.router)
web_router.include_router(fragments.router)
web_router.include_router(health_page.router)
web_router.include_router(irrigators.router)
web_router.include_router(operations.router)
web_router.include_router(pages.router)
web_router.include_router(plants.router)
web_router.include_router(plant_dashboard.router)
web_router.include_router(quality.router)
web_router.include_router(sensors.router)
web_router.include_router(vacation.router)
