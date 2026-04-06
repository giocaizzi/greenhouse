"""APScheduler integration for background tasks."""

from apscheduler.schedulers.background import BackgroundScheduler

from tuya_irrigation_core.database import create_session_factory
from tuya_irrigation_core.repository import IrrigationRepository

scheduler = BackgroundScheduler()

_engine = None


def init_scheduler(engine, settings) -> None:
    """Register default jobs and store engine reference."""
    global _engine
    _engine = engine

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


def _sync_job() -> None:
    """Background job: sync all sensor data."""
    from tuya_irrigation_server.services.sync import sync_all_sensors

    factory = create_session_factory(_engine)
    session = factory()
    try:
        repo = IrrigationRepository(session)
        sync_all_sensors(repo, hours=6)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Sync job error: {e}")
    finally:
        session.close()


def _check_job() -> None:
    """Background job: check all clusters."""
    from tuya_irrigation_server.services.irrigation import check_all_clusters

    factory = create_session_factory(_engine)
    session = factory()
    try:
        repo = IrrigationRepository(session)
        check_all_clusters(repo, dm=None)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Check job error: {e}")
    finally:
        session.close()


def get_jobs() -> list[dict]:
    """List all scheduled jobs."""
    jobs = []
    for job in scheduler.get_jobs():
        next_run = getattr(job, "next_run_time", None)
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "trigger": str(job.trigger),
                "next_run_time": str(next_run) if next_run else None,
            }
        )
    return jobs
