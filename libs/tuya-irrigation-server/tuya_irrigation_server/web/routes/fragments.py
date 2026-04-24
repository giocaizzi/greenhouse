"""HTMX fragment endpoints (polled or generic swap targets)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from tuya_irrigation_server.scheduler import scheduler as bg_scheduler
from tuya_irrigation_server.web.context import base_context
from tuya_irrigation_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/health/badge")
def health_badge(request: Request):
    return templates.TemplateResponse(
        request,
        "partials/_health_badge.html",
        base_context(
            request,
            scheduler_running=bg_scheduler.running,
            job_count=len(bg_scheduler.get_jobs()) if bg_scheduler.running else 0,
        ),
    )
