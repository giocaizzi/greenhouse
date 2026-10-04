"""Web routes for three concerns that share this module (it keeps its name so the route qualnames stay stable).

* Analytics: cluster history, stats, CSV export and the learn/insights page.
* Scheduler page: job table, ad-hoc job delete, ``check_all`` pause/resume.
* Emergency kill switch: ``POST /bulk/stop-all`` (the same service path as the JSON API).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse

from greenhouse_core.stats import get_irrigation_stats
from greenhouse_server.deps import (
    ClusterServiceDep,
    DeviceRegistryDep,
    NtfyNotifierDep,
    PlantDbDep,
    RepoDep,
    WeatherClientDep,
    not_found_as_404,
    require_cluster,
)
from greenhouse_server.scheduler import (
    CoreJobError,
    JobNotRegisteredError,
    core_job_ids,
    delete_job,
    get_jobs,
    is_check_all_paused,
    set_check_all_paused,
)
from greenhouse_server.scheduler import scheduler as bg_scheduler
from greenhouse_server.services.bulk import stop_all_irrigators
from greenhouse_server.services.cluster import cluster_events_csv
from greenhouse_server.services.forecast import ForecastService
from greenhouse_server.services.insights import InsightsService
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

if TYPE_CHECKING:
    from greenhouse_core.repository import IrrigationRepository

router = APIRouter(include_in_schema=False)


@router.get("/clusters/{cluster_id}/history")
def cluster_history(
    request: Request,
    cluster_id: int,
    svc: ClusterServiceDep,
    hours: int = Query(default=24, ge=1),
    limit: int = Query(default=50, ge=1),
) -> Response:
    """Render a cluster's recent readings and irrigation events."""
    with not_found_as_404("Cluster not found"):
        result = svc.get_cluster_history(cluster_id, hours=hours, limit=limit)
    return templates.TemplateResponse(
        request,
        "clusters/history.html",
        base_context(request, history=result, cluster_id=cluster_id, hours=hours, limit=limit),
    )


@router.get("/clusters/{cluster_id}/stats")
def cluster_stats(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    days: int = Query(default=7, ge=1),
) -> Response:
    """Render a cluster's irrigation statistics for the last ``days`` days."""
    cluster = require_cluster(repo, cluster_id)
    stats = get_irrigation_stats(repo, cluster_id, days)
    return templates.TemplateResponse(
        request,
        "clusters/stats.html",
        base_context(request, cluster=cluster, stats=stats, days=days),
    )


@router.get("/clusters/{cluster_id}/stats/export")
def cluster_stats_export(
    cluster_id: int,
    repo: RepoDep,
    days: int = Query(default=7, ge=1),
) -> Response:
    """Download a cluster's irrigation events of the last ``days`` days as CSV."""
    cluster = require_cluster(repo, cluster_id)
    csv_text = cluster_events_csv(repo, cluster_id, days=days)
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=cluster_{cluster.id}_stats.csv"},
    )


@router.get("/clusters/{cluster_id}/learn")
def cluster_learn(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    plant_db: PlantDbDep,
    weather: WeatherClientDep,
) -> Response:
    """Render a cluster's care insights and next-irrigation forecast."""
    cluster = require_cluster(repo, cluster_id)
    insights_resp = InsightsService(repo, plant_db).cluster_insights(cluster_id)
    forecast = ForecastService(repo, plant_db, weather_client=weather).predict_next_irrigation(cluster_id)
    return templates.TemplateResponse(
        request,
        "clusters/learn.html",
        base_context(request, cluster=cluster, insights=insights_resp, forecast=forecast),
    )


@router.get("/scheduler")
def scheduler_page(request: Request) -> Response:
    """Render the scheduler page: running state, jobs (core jobs flagged) and the check-all pause."""
    # Share the API's job serializer so the per-row `paused` badge the
    # template renders is actually populated. Core (built-in) jobs carry
    # `core=True` so the template hides their delete button — deleting them
    # is refused (409); `check_all` is paused instead.
    core = core_job_ids()
    jobs = (
        [{**job, "name": job["name"] or job["id"], "core": job["id"] in core} for job in get_jobs()]
        if bg_scheduler.running
        else []
    )
    return templates.TemplateResponse(
        request,
        "scheduler.html",
        base_context(
            request,
            scheduler_running=bg_scheduler.running,
            jobs=jobs,
            # Truthful on a stopped scheduler too: pause/resume work (and
            # persist) whether or not the scheduler is running.
            check_all_paused=is_check_all_paused(),
        ),
    )


@router.post("/scheduler/jobs/{job_id}/delete", response_class=HTMLResponse)
def scheduler_delete_job(request: Request, job_id: str) -> Response:
    """Delete an ad-hoc scheduler job; the empty body removes its table row."""
    if not bg_scheduler.running:
        raise HTTPException(503, "Scheduler not running")
    try:
        delete_job(job_id)
    except CoreJobError as exc:
        raise HTTPException(409, str(exc)) from exc
    except JobNotRegisteredError as exc:
        raise HTTPException(404, str(exc)) from exc
    # HTMX swap target is `closest tr` — return empty body to remove the row.
    return HTMLResponse("")


def _set_check_all_paused_web(repo: IrrigationRepository, paused: bool) -> RedirectResponse:
    # Same code path as POST /api/v1/scheduler/{pause,resume}: works (and
    # persists the preference) whether or not the scheduler is running.
    try:
        set_check_all_paused(repo, paused)
    except JobNotRegisteredError as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(url="/scheduler", status_code=303)


@router.post("/scheduler/pause")
def scheduler_pause(request: Request, repo: RepoDep) -> Response:
    """Pause the ``check_all`` job (persisted) and return to the scheduler page."""
    return _set_check_all_paused_web(repo, True)


@router.post("/scheduler/resume")
def scheduler_resume(request: Request, repo: RepoDep) -> Response:
    """Resume the ``check_all`` job (persisted) and return to the scheduler page."""
    return _set_check_all_paused_web(repo, False)


@router.post("/bulk/stop-all")
def bulk_stop_all_web(
    request: Request, repo: RepoDep, registry: DeviceRegistryDep, notifier: NtfyNotifierDep
) -> Response:
    """Emergency stop — invoked from dashboard / scheduler.

    HTMX target receives a one-line status fragment so the action is
    auditable inline.
    """
    stopped, errors = stop_all_irrigators(repo, registry, notifier)
    # The stops are committed; drop the uncommitted preferences seed the notify gate may have
    # inserted so the page chrome's own session isn't blocked on SQLite's write lock.
    repo.rollback()
    return templates.TemplateResponse(
        request,
        "partials/_stop_all_result.html",
        base_context(request, stopped=stopped, errors=errors),
    )
