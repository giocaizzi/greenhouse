"""HTMX fragment endpoints (polled or generic swap targets)."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Request

from greenhouse_server.deps import ClusterServiceDep, RepoDep
from greenhouse_server.scheduler import scheduler as bg_scheduler
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

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


@router.get("/dashboard/hero")
def dashboard_hero(request: Request, repo: RepoDep, svc: ClusterServiceDep):
    """Lazy-loaded synthesis line for the dashboard.

    Walks every cluster, asks the irrigation engine for its current decision,
    and reduces the result into a one-sentence summary. Kept out of the main
    dashboard route so the page renders fast and this potentially-expensive
    query streams in via htmx.
    """
    clusters = repo.list_clusters()
    counts: Counter[str] = Counter()
    for cluster in clusters:
        status = svc.get_cluster_status(cluster.id)
        if not status:
            continue
        action = (status.get("decision") or {}).get("action") or "unknown"
        counts[action.lower()] += 1
    return templates.TemplateResponse(
        request,
        "partials/_dashboard_hero.html",
        base_context(request, total=len(clusters), counts=dict(counts)),
    )
