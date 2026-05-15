"""Top-level web router that assembles all sub-routers.

Every page sits behind `require_web_user`, which redirects unauthenticated
browsers to `/login`. The login page itself is registered first without the
dependency — it must be reachable without a session.
"""

from fastapi import APIRouter, Depends

from greenhouse_server.auth import require_web_user
from greenhouse_server.web.routes import (
    activity,
    alerts,
    analytics,
    clusters,
    configs,
    decisions,
    efficacy,
    fragments,
    health_page,
    irrigators,
    operations,
    pages,
    plant_dashboard,
    plants,
    preferences,
    quality,
    sensors,
    vacation,
    windows,
)
from greenhouse_server.web.routes import (
    auth as web_auth,
)

web_router = APIRouter(include_in_schema=False)

# Login form + POST: must be reachable without a session.
web_router.include_router(web_auth.router)

# Authenticated pages — `require_web_user` 303-redirects to /login on miss.
protected = [Depends(require_web_user)]
web_router.include_router(activity.router, dependencies=protected)
web_router.include_router(alerts.router, dependencies=protected)
web_router.include_router(analytics.router, dependencies=protected)
web_router.include_router(clusters.router, dependencies=protected)
web_router.include_router(configs.router, dependencies=protected)
web_router.include_router(decisions.router, dependencies=protected)
web_router.include_router(efficacy.router, dependencies=protected)
web_router.include_router(fragments.router, dependencies=protected)
web_router.include_router(health_page.router, dependencies=protected)
web_router.include_router(irrigators.router, dependencies=protected)
web_router.include_router(operations.router, dependencies=protected)
web_router.include_router(pages.router, dependencies=protected)
web_router.include_router(plants.router, dependencies=protected)
web_router.include_router(plant_dashboard.router, dependencies=protected)
web_router.include_router(preferences.router, dependencies=protected)
web_router.include_router(quality.router, dependencies=protected)
web_router.include_router(sensors.router, dependencies=protected)
web_router.include_router(vacation.router, dependencies=protected)
web_router.include_router(windows.router, dependencies=protected)
