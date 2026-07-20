"""APScheduler integration for background tasks."""

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from greenhouse_core.constants import HEALTH_POLL_IDLE_MINUTES
from greenhouse_core.devices import DeviceGateway
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.config import Settings

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

_app: FastAPI | None = None

# Cron jobs that gate wall-clock-sensitive work and therefore MUST fire on the
# same clock the engine reasons in (UserPreferences.timezone). These are
# re-added by `reschedule_for_timezone` whenever the preference changes.
_TZ_BOUND_CRON_JOBS = ("check_all", "plant_health_snapshot")


def _resolve_zoneinfo(tz_name: str | None) -> ZoneInfo:
    """Resolve a tz name to a ZoneInfo, falling back to UTC on bad input.

    Mirrors `greenhouse_core.logic.timing._resolve_tz` so the scheduler and the
    engine agree on the same fallback when a preference holds an unknown zone.
    """
    if not tz_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


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


def init_scheduler(app: FastAPI, settings: Settings, tz_name: str | None = None) -> None:
    """Register default jobs and store app reference for state access.

    Args:
        app: The FastAPI app whose ``state`` the jobs read.
        settings: Server settings (intervals, cron cadence).
        tz_name: The authoritative timezone (``UserPreferences.timezone``).
            The scheduler is configured to this zone so the wall-clock cron
            jobs (``check_all``, the daily health snapshot) fire on the same
            clock the engine gates windows against. Falls back to UTC when
            unset or unknown — never the host zone.
    """
    global _app
    _app = app

    scheduler.configure(timezone=_resolve_zoneinfo(tz_name))

    scheduler.add_job(
        _sync_job,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="sensor_sync",
        name="Sensor data sync",
        replace_existing=True,
    )
    _add_tz_bound_cron_jobs(settings)
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


def _add_tz_bound_cron_jobs(settings: Settings) -> None:
    """(Re-)register the wall-clock cron jobs against the scheduler's timezone.

    APScheduler binds a cron trigger's timezone at add-time, so changing the
    scheduler's configured zone only takes effect for jobs added afterwards.
    Both registration (`init_scheduler`) and the on-preference-change reschedule
    (`reschedule_for_timezone`) route through here so the two stay identical.
    """
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


def reschedule_for_timezone(tz_name: str | None, settings: Settings) -> None:
    """Re-point the scheduler at ``tz_name`` and rebuild the cron jobs.

    Called when ``UserPreferences.timezone`` changes so the wall-clock cron
    jobs keep firing on the same clock the engine reasons in. A paused
    ``check_all`` stays paused — the re-add preserves nothing about run state,
    so the persisted-pause flag is re-applied afterward.

    Args:
        tz_name: The new authoritative timezone (``UserPreferences.timezone``).
        settings: Server settings (supplies the cron cadence).
    """
    paused = is_check_all_paused()
    # Assign the attribute directly rather than calling ``configure()``: the
    # scheduler is already running here and ``configure()`` raises in that
    # state. ``_create_trigger`` reads ``scheduler.timezone`` at add-time.
    scheduler.timezone = _resolve_zoneinfo(tz_name)
    # Remove-then-add (not ``replace_existing``) so the cron triggers are
    # rebuilt from the new zone — APScheduler's replace path leaves a stopped
    # scheduler's pending trigger bound to the old tz.
    for job_id in _TZ_BOUND_CRON_JOBS:
        if scheduler.get_job(job_id) is not None:
            scheduler.remove_job(job_id)
    _add_tz_bound_cron_jobs(settings)
    if paused:
        apply_persisted_pause(True)


def apply_timezone_preference(request, tz_name: str | None) -> None:
    """Re-sync every clock to ``UserPreferences.timezone`` after it changes.

    Keeps the three formerly-competing clocks in lockstep with the engine:
    the scheduler's wall-clock cron jobs, the weather forecast localization,
    and the display formatter. A no-op when the timezone is unchanged.

    Args:
        request: The active FastAPI request (carries ``app.state`` and
            ``app.state.settings``).
        tz_name: The new ``UserPreferences.timezone`` value.
    """
    from greenhouse_core.utils import get_display_timezone, set_display_timezone
    from greenhouse_server.services.weather import WeatherClient

    if (tz_name or "UTC") == get_display_timezone():
        return

    app = request.app
    settings = app.state.settings

    set_display_timezone(tz_name)
    reschedule_for_timezone(tz_name, settings)
    app.state.weather_client = WeatherClient(
        lat=settings.weather_lat,
        lon=settings.weather_lon,
        tz=tz_name or "UTC",
    )


def _get_cloud() -> DeviceGateway | None:
    """Return the one app-scoped Tuya gateway (shared client/token), or None.

    Background jobs borrow ``app.state.device_gateway`` rather than building a
    fresh client per tick — so a sync/check run costs no extra ``/v1.0/token``
    call, and the local-key cache warmed by one job is seen by the others.
    """
    return getattr(_app.state, "device_gateway", None) if _app is not None else None


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
            notifier=getattr(_app.state, "ntfy_notifier", None),
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
        SensorAnomalyService(repo, notifier=getattr(_app.state, "ntfy_notifier", None)).scan()
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
        monitor = DeviceHealthMonitor(repo=repo, registry=registry, notifier=getattr(app.state, "ntfy_notifier", None))
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
