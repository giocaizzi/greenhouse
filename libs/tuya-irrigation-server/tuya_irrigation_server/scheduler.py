"""APScheduler integration for background tasks."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from tuya_irrigation_core.cloud import TuyaCloud
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_server.config import Settings

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

_app: FastAPI | None = None


def init_scheduler(app: FastAPI, settings: Settings) -> None:
    """Register default jobs and store app reference for state access."""
    global _app
    _app = app

    scheduler.add_job(
        _sync_job,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="sensor_sync",
        name="Sensor data sync",
        replace_existing=True,
    )
    scheduler.add_job(
        _check_job,
        "interval",
        hours=settings.check_interval_hours,
        id="check_all",
        name="Check all clusters",
        replace_existing=True,
    )


def _get_cloud() -> TuyaCloud | None:
    """Create TuyaCloud client, returning None if credentials are missing."""
    try:
        return TuyaCloud()
    except (ValueError, Exception):
        return None


def _sync_job() -> None:
    """Background job: sync all sensor data."""
    from tuya_irrigation_server.services.sync import SyncService

    cloud = _get_cloud()
    if cloud is None:
        logger.debug("Sync job skipped: no Tuya credentials")
        return

    session = _app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        sync_svc = SyncService(repo, cloud)
        sync_svc.sync_all_sensors(hours=6)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Sync job failed")
    finally:
        session.close()


def _check_job() -> None:
    """Background job: check all clusters."""
    from tuya_irrigation_server.services.irrigation import IrrigationService
    from tuya_irrigation_server.services.sync import SyncService

    cloud = _get_cloud()

    session = _app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        sync_svc = SyncService(repo, cloud)
        irrigation_svc = IrrigationService(
            repo=repo,
            dm=_app.state.device_manager,
            sync_service=sync_svc,
            weather_client=_app.state.weather_client,
            plant_db=_app.state.plant_db,
        )
        irrigation_svc.check_all_clusters()
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Check job failed")
    finally:
        session.close()


def get_jobs() -> list[dict]:
    """List all scheduled jobs."""
    return [
        {
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run_time": str(next_run) if (next_run := getattr(job, "next_run_time", None)) else None,
        }
        for job in scheduler.get_jobs()
    ]
