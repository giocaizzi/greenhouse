"""Top-level full-page routes (dashboard, health, scheduler index)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from tuya_irrigation_server.deps import RepoDep
from tuya_irrigation_server.scheduler import scheduler as bg_scheduler
from tuya_irrigation_server.web.context import base_context
from tuya_irrigation_server.web.templating import templates

router = APIRouter()


@router.get("/", include_in_schema=False)
def dashboard(request: Request, repo: RepoDep):
    clusters = repo.list_clusters()
    return templates.TemplateResponse(request, "dashboard.html", base_context(request, clusters=clusters))


@router.get("/health", include_in_schema=False)
def health_page(request: Request):
    jobs = []
    if bg_scheduler.running:
        for job in bg_scheduler.get_jobs():
            jobs.append(
                {
                    "id": job.id,
                    "name": job.name or job.id,
                    "trigger": str(job.trigger),
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                }
            )
    return templates.TemplateResponse(
        request,
        "health.html",
        base_context(request, scheduler_running=bg_scheduler.running, jobs=jobs),
    )
