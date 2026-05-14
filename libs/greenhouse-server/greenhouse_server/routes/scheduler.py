"""Scheduler management routes."""

from fastapi import APIRouter, HTTPException

from greenhouse_core.schemas import (
    HealthResponse,
    SchedulerJobResponse,
    SchedulerStateResponse,
    SuccessResponse,
)
from greenhouse_server.deps import RepoDep
from greenhouse_server.scheduler import (
    CHECK_ALL_JOB_ID,
    get_jobs,
    is_check_all_paused,
    scheduler,
)

router = APIRouter(tags=["scheduler"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe: server status, scheduler state, and registered jobs.

    Returns:
        `status="ok"` and a snapshot of the APScheduler jobs the server
        currently has registered.
    """
    return HealthResponse(
        status="ok",
        scheduler_running=scheduler.running,
        jobs=[SchedulerJobResponse(**j) for j in get_jobs()],
    )


@router.get("/scheduler/jobs", response_model=list[SchedulerJobResponse])
def list_jobs() -> list[SchedulerJobResponse]:
    """List every background job registered with APScheduler.

    Returns:
        One entry per job with its id, name, trigger description,
        next_run_time (null when paused), and a `paused` flag.
    """
    return [SchedulerJobResponse(**j) for j in get_jobs()]


@router.delete("/scheduler/jobs/{job_id}", response_model=SuccessResponse)
def delete_job(job_id: str) -> SuccessResponse:
    """Unregister a background job by ID.

    Side effects: the job stops firing immediately. Any in-flight execution
    is allowed to complete; deleting a job mid-run does not abort it.

    Args:
        job_id: APScheduler job identifier (see GET /scheduler/jobs).

    Raises:
        HTTPException: 404 if no job with that ID is registered.
    """
    try:
        scheduler.remove_job(job_id)
        return SuccessResponse(success=True)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None


@router.post("/scheduler/pause", response_model=SchedulerStateResponse)
def pause_scheduler(repo: RepoDep) -> SchedulerStateResponse:
    """Pause the `check_all` scheduler job and persist the flag.

    Stops new check-all runs from firing. Sensor sync, anomaly scan, and
    plant-health snapshot jobs are unaffected. The pause flag is persisted
    to `user_preferences.scheduler_paused` so the pause survives a server
    restart — see startup wiring in `app.py`.

    Returns:
        Current paused state of the `check_all` job (always True on success).

    Raises:
        HTTPException: 404 if the `check_all` job is not registered.
    """
    job = scheduler.get_job(CHECK_ALL_JOB_ID)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {CHECK_ALL_JOB_ID} not found")
    try:
        scheduler.pause_job(CHECK_ALL_JOB_ID)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to pause job: {exc}") from exc
    repo.update_preferences(scheduler_paused=True)
    repo.session.commit()
    return SchedulerStateResponse(paused=True)


@router.post("/scheduler/resume", response_model=SchedulerStateResponse)
def resume_scheduler(repo: RepoDep) -> SchedulerStateResponse:
    """Resume the `check_all` scheduler job and clear the persisted pause.

    Reverses a prior /scheduler/pause. Has no effect if the job was already
    running. Other background jobs are not touched.

    Returns:
        Current paused state of the `check_all` job (always False on success).

    Raises:
        HTTPException: 404 if the `check_all` job is not registered.
    """
    job = scheduler.get_job(CHECK_ALL_JOB_ID)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {CHECK_ALL_JOB_ID} not found")
    try:
        scheduler.resume_job(CHECK_ALL_JOB_ID)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to resume job: {exc}") from exc
    repo.update_preferences(scheduler_paused=False)
    repo.session.commit()
    return SchedulerStateResponse(paused=is_check_all_paused())
