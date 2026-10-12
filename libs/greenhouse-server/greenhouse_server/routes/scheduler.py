"""Scheduler management routes."""

from fastapi import APIRouter, HTTPException

from greenhouse_core.schemas import (
    HealthResponse,
    SchedulerJobResponse,
    SchedulerStateResponse,
    SuccessResponse,
)
from greenhouse_server import scheduler as sched
from greenhouse_server.deps import RepoDep
from greenhouse_server.scheduler import get_jobs, scheduler

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
        `next_run_time` (null when the job is paused or the scheduler is not
        running — see `scheduler_running` on GET /health), and a `paused`
        flag that is true only for an explicitly paused job (`check_all`
        after POST /scheduler/pause, mirroring the persisted preference).
    """
    return [SchedulerJobResponse(**j) for j in get_jobs()]


@router.delete("/scheduler/jobs/{job_id}", response_model=SuccessResponse)
def delete_job(job_id: str) -> SuccessResponse:
    """Unregister an ad-hoc background job by ID.

    Only ad-hoc one-shot jobs (per-irrigation pump watchers and leak checks)
    can be deleted. The built-in jobs registered at startup (`check_all`,
    sensor sync, anomaly scan, device-health monitor, plant-health snapshot)
    are refused with 409: to stop automatic irrigation checks, use
    POST /scheduler/pause instead.

    Side effects: the job stops firing immediately. Any in-flight execution
    is allowed to complete; deleting a job mid-run does not abort it. Works
    whether or not the scheduler is running.

    Args:
        job_id: APScheduler job identifier (see GET /scheduler/jobs).

    Returns:
        `success=True` once the job is unregistered.

    Raises:
        HTTPException: 404 if no job with that ID is registered, 409 if the
            job is a built-in scheduler job.
    """
    try:
        sched.delete_job(job_id)
    except sched.CoreJobError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except sched.JobNotRegisteredError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return SuccessResponse(success=True)


def _set_paused(repo, paused: bool) -> SchedulerStateResponse:
    try:
        return SchedulerStateResponse(paused=sched.set_check_all_paused(repo, paused))
    except sched.JobNotRegisteredError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except Exception as exc:
        verb = "pause" if paused else "resume"
        raise HTTPException(status_code=500, detail=f"Failed to {verb} job: {exc}") from exc


@router.post("/scheduler/pause", response_model=SchedulerStateResponse)
def pause_scheduler(repo: RepoDep) -> SchedulerStateResponse:
    """Pause the `check_all` scheduler job and persist the flag.

    Stops new check-all runs from firing. Sensor sync, anomaly scan,
    device-health monitor, and plant-health snapshot jobs are unaffected.
    The pause flag is persisted to `user_preferences.scheduler_paused` so the
    pause survives a server restart — see startup wiring in `app.py`. Works
    whether or not the background scheduler is running.

    Returns:
        Current paused state of the `check_all` job (always True on success).

    Raises:
        HTTPException: 404 if the `check_all` job is not registered.
    """
    return _set_paused(repo, True)


@router.post("/scheduler/resume", response_model=SchedulerStateResponse)
def resume_scheduler(repo: RepoDep) -> SchedulerStateResponse:
    """Resume the `check_all` scheduler job and clear the persisted pause.

    Reverses a prior /scheduler/pause. Has no effect if the job was already
    running. Other background jobs are not touched. Works whether or not the
    background scheduler is running.

    Returns:
        Current paused state of the `check_all` job (always False on success).

    Raises:
        HTTPException: 404 if the `check_all` job is not registered.
    """
    return _set_paused(repo, False)
