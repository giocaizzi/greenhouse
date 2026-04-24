"""Top-level web router that assembles all sub-routers."""

from fastapi import APIRouter

from tuya_irrigation_server.web.routes import clusters, fragments, pages

web_router = APIRouter(include_in_schema=False)
web_router.include_router(pages.router)
web_router.include_router(fragments.router)
web_router.include_router(clusters.router)
