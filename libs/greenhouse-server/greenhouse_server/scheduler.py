"""APScheduler integration for background tasks."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from greenhouse_core.cloud import TuyaCloud
from greenhouse_core.constants import HEALTH_POLL_IDLE_MINUTES
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.config import Settings

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

_app: FastAPI | None = None


def _resolve_check_cron_hours(settings: Settings) -> str:
    """Pick the cron `hour` field, honoring the deprecated interval var.

    Why a shim: the project switched check_all from APScheduler's `interval`
    trigger to `cron` for predictable wall-clock fires. Operators with
    `IRRIGATION_CHECK_INTERVAL_HOURS=N` already set in their .env shouldn't
    silently lose their cadence — translate `N` to `*/N` cron syntax and
    warn once. An explicit `IRRIGATION_CHECK_CRON_HOURS` always wins.
    """
    if settings.check_interval_hours is not None and settings.check_cron_hours == "*":
        n = settings.check_interval_hours
        logger.warning(
            "IRRIGATION_CHECK_INTERVAL_HOURS is deprecated; set "
            "IRRIGATION_CHECK_CRON_HOURS instead. Translating value %d to '*/%d'.",
            n,
            n,
        )
        return f"*/{n}"
    return settings.check_cron_hours


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
        "cron",
        hour=_resolve_check_cron_hours(settings),
        minute=0,
        id="check_all",
        name="Check all clusters",
        replace_existing=True,
    )
    scheduler.add_job(
        _health_snapshot_job,
        "cron",
        hour=0,
        minute=30,
        id="plant_health_snapshot",
        name="Daily plant health snapshot",
        replace_existing=True,
    )
    scheduler.add_job(
        _anomaly_job,
        "interval",
        minutes=15,
        id="sensor_anomaly",
        name="Sensor anomaly scan",
        replace_existing=True,
    )
    scheduler.add_job(
        _health_monitor_job,
        "interval",
        minutes=HEALTH_POLL_IDLE_MINUTES,
        id="device_health_monitor",
        name="Device health monitor",
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
    from greenhouse_server.services.sync import SyncService

    cloud = _get_cloud()
    if cloud is None:
        logger.debug("Sync job skipped: no Tuya credentials")
        return

    registry = getattr(_app.state, "device_registry", None)
    session = _app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        sync_svc = SyncService(repo, registry, cloud)
        sync_svc.sync_all_sensors(hours=6)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Sync job failed")
    finally:
        session.close()


def _health_snapshot_job() -> None:
    """Background job: compute and persist daily plant health snapshots."""
    from greenhouse_server.services.health import PlantHealthService

    session = _app.state.session_factory()
    try:
        from greenhouse_core.repository import IrrigationRepository

        repo = IrrigationRepository(session)
        svc = PlantHealthService(repo, _app.state.plant_db)
        svc.snapshot_daily()
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Plant health snapshot job failed")
    finally:
        session.close()


def _check_job() -> None:
    """Background job: check all clusters."""
    from greenhouse_server.services.irrigation import IrrigationService
    from greenhouse_server.services.sync import SyncService

    cloud = _get_cloud()
    registry = getattr(_app.state, "device_registry", None)

    session = _app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        sync_svc = SyncService(repo, registry, cloud)
        monitor = getattr(_app.state, "health_monitor", None)
        if monitor is not None:
            monitor.bind_repo(repo)
        irrigation_svc = IrrigationService(
            repo=repo,
            registry=registry,
            sync_service=sync_svc,
            weather_client=_app.state.weather_client,
            plant_db=_app.state.plant_db,
            health_monitor=monitor,
        )
        irrigation_svc.check_all_clusters()
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Check job failed")
    finally:
        session.close()


def _anomaly_job() -> None:
    """Background job: scan all sensors for staleness and drift anomalies."""
    from greenhouse_server.services.anomaly import SensorAnomalyService

    session = _app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        SensorAnomalyService(repo).scan()
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Anomaly scan job failed")
    finally:
        session.close()


def _health_monitor_job() -> None:
    """Background job: poll every device's health surface, diff, alert.

    Reuses the singleton :class:`DeviceHealthMonitor` stored on
    ``app.state.health_monitor`` (built at startup by
    :func:`init_health_monitor`) so the in-memory transition cache
    survives across ticks. Falls open silently when no registry is wired.
    """
    if _app is None:
        return

    monitor = getattr(_app.state, "health_monitor", None)
    if monitor is None:
        logger.debug("Health monitor job skipped: no monitor wired")
        return

    session = _app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        monitor.bind_repo(repo)
        monitor.poll_all()
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Device health monitor job failed")
    finally:
        session.close()


def init_health_monitor(app: FastAPI, settings: Settings) -> None:
    """Build the long-lived :class:`DeviceHealthMonitor` for this app.

    Stored on ``app.state.health_monitor`` so dependency-injection wiring
    (:func:`greenhouse_server.deps.get_health_monitor`) and the scheduler
    job share one instance — its cache is the engine's source of truth
    for actuation gating. Falls open if there is no device registry wired
    (tests usually omit it).
    """
    from greenhouse_server.services.health_monitor import DeviceHealthMonitor

    registry = getattr(app.state, "device_registry", None)
    if registry is None:
        logger.debug("Health monitor init skipped: no device registry")
        return

    session = app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        monitor = DeviceHealthMonitor(repo=repo, registry=registry)
        try:
            migrated = monitor.migrate_legacy_pump_alerts()
            if migrated:
                logger.info("Migrated %d legacy pump_dry_run alerts to health: keys", migrated)
            monitor.backfill_from_history()
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Health monitor startup hooks failed")
        app.state.health_monitor = monitor
    finally:
        session.close()


CHECK_ALL_JOB_ID = "check_all"


def get_jobs() -> list[dict]:
    """List all scheduled jobs.

    APScheduler marks a paused job by clearing its ``next_run_time``; we
    surface that as a ``paused`` flag so the UI/CLI/MCP do not have to
    introspect the trigger.
    """
    return [
        {
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run_time": str(next_run) if (next_run := getattr(job, "next_run_time", None)) else None,
            "paused": getattr(job, "next_run_time", None) is None,
        }
        for job in scheduler.get_jobs()
    ]


def is_check_all_paused() -> bool:
    """True when the `check_all` job is currently paused."""
    job = scheduler.get_job(CHECK_ALL_JOB_ID)
    if job is None:
        return False
    return getattr(job, "next_run_time", None) is None


def apply_persisted_pause(persisted_paused: bool) -> None:
    """Re-apply the persisted pause flag to the live scheduler.

    Called once on startup after `init_scheduler` has registered jobs so
    the pause survives a process restart. The scheduler does not have to
    be running yet — APScheduler honors `pause_job` on stopped schedulers
    by clearing the job's `next_run_time`.
    """
    if not persisted_paused:
        return
    job = scheduler.get_job(CHECK_ALL_JOB_ID)
    if job is None:
        return
    try:
        scheduler.pause_job(CHECK_ALL_JOB_ID)
    except Exception:
        logger.exception("Failed to re-apply persisted scheduler pause")
