"""Scheduler management routes."""

from fastapi import APIRouter, HTTPException

from greenhouse_core.schemas import HealthResponse, SchedulerJobResponse, SuccessResponse
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
    """List every background job registered with APScheduler."""
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
