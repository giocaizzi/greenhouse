"""System health full-page web route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from tuya_irrigation_server.deps import RepoDep, SyncServiceDep
from tuya_irrigation_server.services.system_health import SystemHealthService
from tuya_irrigation_server.web.context import base_context
from tuya_irrigation_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/health")
def health_page(request: Request, repo: RepoDep, sync_svc: SyncServiceDep):
    svc = SystemHealthService(repo, sync_svc)
    pulse = svc.pulse()
    return templates.TemplateResponse(
        request,
        "health.html",
        base_context(request, pulse=pulse),
    )
