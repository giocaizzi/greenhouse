"""Scheduler management routes."""

from fastapi import APIRouter, HTTPException

from tuya_irrigation_core.schemas import HealthResponse, SchedulerJobResponse
from tuya_irrigation_server.scheduler import get_jobs, scheduler

router = APIRouter(tags=["scheduler"])


@router.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        scheduler_running=scheduler.running,
        jobs=[SchedulerJobResponse(**j) for j in get_jobs()],
    )


@router.get("/scheduler/jobs", response_model=list[SchedulerJobResponse])
def list_jobs():
    return [SchedulerJobResponse(**j) for j in get_jobs()]


@router.delete("/scheduler/jobs/{job_id}")
def delete_job(job_id: str):
    try:
        scheduler.remove_job(job_id)
        return {"success": True}
    except Exception:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
