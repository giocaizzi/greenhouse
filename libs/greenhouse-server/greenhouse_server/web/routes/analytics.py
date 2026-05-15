"""Web analytics routes: history, stats, CSV export, learn, scheduler."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from greenhouse_core.stats import get_irrigation_stats
from greenhouse_core.utils import format_timestamp
from greenhouse_server.deps import (
    ClusterServiceDep,
    DeviceManagerDep,
    PlantDbDep,
    RepoDep,
    WeatherClientDep,
    require_cluster,
)
from greenhouse_server.scheduler import CHECK_ALL_JOB_ID, is_check_all_paused
from greenhouse_server.scheduler import scheduler as bg_scheduler
from greenhouse_server.services.bulk import stop_all_irrigators
from greenhouse_server.services.forecast import ForecastService
from greenhouse_server.services.insights import InsightsService
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/clusters/{cluster_id}/history")
def cluster_history(
    request: Request,
    cluster_id: int,
    svc: ClusterServiceDep,
    hours: int = Query(default=24, ge=1),
    limit: int = Query(default=50, ge=1),
):
    result = svc.get_cluster_history(cluster_id, hours=hours, limit=limit)
    if not result:
        raise HTTPException(404, "Cluster not found")
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
):
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
):
    cluster = require_cluster(repo, cluster_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "date", "time", "irrigator", "action", "duration_minutes", "triggered_by", "notes"])
    for irrigator in repo.get_irrigators_in_cluster(cluster_id):
        for event in repo.get_recent_events(irrigator.id, hours=days * 24):
            ts_str = format_timestamp(event.timestamp)
            date, _, time_part = ts_str.partition(" ")
            writer.writerow(
                [
                    event.timestamp,
                    date,
                    time_part,
                    irrigator.name,
                    event.action,
                    event.duration_minutes or "",
                    event.triggered_by,
                    event.notes or "",
                ]
            )
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
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
):
    cluster = require_cluster(repo, cluster_id)
    insights_resp = InsightsService(repo, plant_db).cluster_insights(cluster_id)
    forecast = ForecastService(repo, plant_db, weather_client=weather).predict_next_irrigation(cluster_id)
    return templates.TemplateResponse(
        request,
        "clusters/learn.html",
        base_context(request, cluster=cluster, insights=insights_resp, forecast=forecast),
    )


@router.get("/scheduler")
def scheduler_page(request: Request):
    jobs: list[dict] = []
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
    check_paused = is_check_all_paused() if bg_scheduler.running else False
    return templates.TemplateResponse(
        request,
        "scheduler.html",
        base_context(
            request,
            scheduler_running=bg_scheduler.running,
            jobs=jobs,
            check_all_paused=check_paused,
        ),
    )


@router.post("/scheduler/jobs/{job_id}/delete", response_class=HTMLResponse)
def scheduler_delete_job(request: Request, job_id: str):
    if not bg_scheduler.running:
        raise HTTPException(503, "Scheduler not running")
    try:
        bg_scheduler.remove_job(job_id)
    except Exception as exc:
        raise HTTPException(404, f"Job not found: {exc}") from exc
    # HTMX swap target is `closest tr` — return empty body to remove the row.
    return HTMLResponse("")


@router.post("/scheduler/pause")
def scheduler_pause(request: Request, repo: RepoDep):
    if not bg_scheduler.running:
        raise HTTPException(503, "Scheduler not running")
    job = bg_scheduler.get_job(CHECK_ALL_JOB_ID)
    if job is None:
        raise HTTPException(404, f"Job {CHECK_ALL_JOB_ID} not found")
    bg_scheduler.pause_job(CHECK_ALL_JOB_ID)
    repo.update_preferences(scheduler_paused=True)
    repo.session.commit()
    return RedirectResponse(url="/scheduler", status_code=303)


@router.post("/scheduler/resume")
def scheduler_resume(request: Request, repo: RepoDep):
    if not bg_scheduler.running:
        raise HTTPException(503, "Scheduler not running")
    job = bg_scheduler.get_job(CHECK_ALL_JOB_ID)
    if job is None:
        raise HTTPException(404, f"Job {CHECK_ALL_JOB_ID} not found")
    bg_scheduler.resume_job(CHECK_ALL_JOB_ID)
    repo.update_preferences(scheduler_paused=False)
    repo.session.commit()
    return RedirectResponse(url="/scheduler", status_code=303)


@router.post("/bulk/stop-all")
def bulk_stop_all_web(request: Request, repo: RepoDep, dm: DeviceManagerDep):
    """Emergency stop — invoked from dashboard / scheduler.

    HTMX target receives a one-line status fragment so the action is
    auditable inline.
    """
    stopped, errors = stop_all_irrigators(repo, dm)
    return templates.TemplateResponse(
        request,
        "partials/_stop_all_result.html",
        base_context(request, stopped=stopped, errors=errors),
    )
