"""System health full-page web route."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from greenhouse_server.deps import SystemHealthServiceDep
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/health")
def health_page(request: Request, svc: SystemHealthServiceDep) -> Response:
    """Render the system health page (sensor freshness, devices, Cloud reachability)."""
    pulse = svc.pulse()
    return templates.TemplateResponse(
        request,
        "health.html",
        base_context(request, pulse=pulse),
    )
