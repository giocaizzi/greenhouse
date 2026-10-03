"""APScheduler integration for background tasks."""

import logging
import threading
from typing import TYPE_CHECKING, Any, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from greenhouse_core.constants import (
    ANOMALY_SCAN_INTERVAL_MINUTES,
    HEALTH_POLL_IDLE_MINUTES,
    HEALTH_SNAPSHOT_HOUR,
    HEALTH_SNAPSHOT_MINUTE,
    SYNC_JOB_BACKFILL_HOURS,
)
from greenhouse_core.devices import DeviceGateway
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.config import Settings
from greenhouse_server.services._session import job_session as _job_session  # private: keeps the frozen dir() surface

if TYPE_CHECKING:
    from collections.abc import Callable

    from apscheduler.job import Job
    from starlette.requests import Request

    from greenhouse_core.devices import DeviceRegistry
    from greenhouse_server.services.irrigation import IrrigationService

logger = logging.getLogger(__name__)

# Defaults for every job, periodic and one-shot alike. APScheduler's stock
# ``misfire_grace_time`` is 1 second: a run whose dispatch is even slightly late
# is *dropped* with only a warning. That silently skipped every pump watcher
# (its ``date`` run is the already-elapsed start timestamp) and can skip a
# check_all tick under load or after a host suspend. ``None`` = run however
# late; ``coalesce`` collapses a backlog to one run; ``max_instances=1`` keeps
# the stock no-overlap guarantee. Passed to both the constructor and every
# ``configure()`` call, because ``configure()`` resets unspecified defaults.
_JOB_DEFAULTS = {"misfire_grace_time": None, "coalesce": True, "max_instances": 1}

scheduler = BackgroundScheduler(job_defaults=_JOB_DEFAULTS)

_app: FastAPI | None = None

# Set when the app shuts down. Long-running jobs (the pump watcher runs for
# the whole irrigation) poll it and sleep on it, so shutdown can interrupt
# them: APScheduler's worker threads are non-daemon and the interpreter joins
# them at exit, so an uninterrupted watcher held the process open for the
# rest of the irrigation.
_shutdown_event = threading.Event()


def shutdown_requested() -> bool:
    """True once the server has begun shutting down the scheduler."""
    return _shutdown_event.is_set()


def wait_for_shutdown(seconds: float) -> bool:
    """Sleep up to ``seconds``, waking early on shutdown; True if shutting down."""
    return _shutdown_event.wait(seconds)


def start_scheduler(*, paused: bool = False) -> None:
    """Start the background scheduler (clearing any earlier shutdown signal)."""
    _shutdown_event.clear()
    scheduler.start(paused=paused)


def stop_scheduler() -> None:
    """Signal running jobs to wind down, then stop the scheduler without waiting.

    Interrupted pump watchers apply the shutdown policy in
    ``services.irrigation.handle_watcher_interrupted`` on their own thread;
    the interpreter joins those threads at exit, which now takes about one
    device call rather than the remainder of the irrigation.
    """
    _shutdown_event.set()
    if scheduler.running:
        scheduler.shutdown(wait=False)


CHECK_ALL_JOB_ID = "check_all"

# Cron jobs that gate wall-clock-sensitive work and therefore MUST fire on the
# same clock the engine reasons in (UserPreferences.timezone). These are
# re-added by `reschedule_for_timezone` whenever the preference changes.
_TZ_BOUND_CRON_JOBS = (CHECK_ALL_JOB_ID, "plant_health_snapshot")

# IDs of the built-in jobs registered at startup, recorded by `_add_core_job`
# as they are registered — so the protected set can never drift from what
# `init_scheduler` actually adds. Core jobs cannot be deleted at runtime
# (that silently disabled e.g. auto-irrigation until restart); `check_all`
# is paused instead. Ad-hoc one-shot jobs (pump watchers, leak checks) stay
# deletable.
_CORE_JOB_IDS: set[str] = set()


class CoreJobError(Exception):
    """Raised when a caller tries to delete a built-in (core) scheduler job."""


class JobNotRegisteredError(LookupError):
    """Raised when the job an operation targets is not registered."""


def _add_core_job(
    func: "Callable[[], None]",
    trigger: str,
    *,
    id: str,  # noqa: A002 — mirrors APScheduler add_job(id=...) at every call site
    name: str,
    **trigger_args: Any,
) -> None:
    """Register (or replace) a built-in job and mark its id as core."""
    _CORE_JOB_IDS.add(id)
    scheduler.add_job(func, trigger, id=id, name=name, replace_existing=True, **trigger_args)


def core_job_ids() -> frozenset[str]:
    """IDs of the built-in jobs registered at startup (protected from deletion)."""
    return frozenset(_CORE_JOB_IDS)


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
    """The cron ``hour`` field for ``check_all`` (``IRRIGATION_CHECK_CRON_HOURS``, validated by Settings).

    Kept as the one seam the registration reads (tests patch it by name).
    """
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

    # ``configure()`` requires a stopped scheduler (it raises otherwise, before
    # touching anything) and wipes the job stores.
    scheduler.configure(timezone=_resolve_zoneinfo(tz_name), job_defaults=_JOB_DEFAULTS)
    # The scheduler is process-wide, so a second ``create_app`` in the same
    # process (tests, embedding) re-runs this. Jobs added before ``start()``
    # sit in APScheduler's pending list, which ``replace_existing`` does NOT
    # dedupe — every rebuild appended another copy of each job, and a
    # persisted pause re-applied to the first (stale) copy was undone at
    # start by the newest one. Clear it so registration is idempotent and the
    # jobs belong to this app only.
    scheduler.remove_all_jobs()

    _add_core_job(
        _sync_job,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="sensor_sync",
        name="Sensor data sync",
    )
    _add_tz_bound_cron_jobs(settings)
    _add_core_job(
        _anomaly_job,
        "interval",
        minutes=ANOMALY_SCAN_INTERVAL_MINUTES,
        id="sensor_anomaly",
        name="Sensor anomaly scan",
    )
    _add_core_job(
        _health_monitor_job,
        "interval",
        minutes=HEALTH_POLL_IDLE_MINUTES,
        id="device_health_monitor",
        name="Device health monitor",
    )


def _add_tz_bound_cron_jobs(settings: Settings) -> None:
    """(Re-)register the wall-clock cron jobs against the scheduler's timezone.

    APScheduler binds a cron trigger's timezone at add-time, so changing the
    scheduler's configured zone only takes effect for jobs added afterwards.
    Both registration (`init_scheduler`) and the on-preference-change reschedule
    (`reschedule_for_timezone`) route through here so the two stay identical.
    """
    _add_core_job(
        _check_job,
        "cron",
        hour=_resolve_check_cron_hours(settings),
        minute=0,
        id=CHECK_ALL_JOB_ID,
        name="Check all clusters",
    )
    _add_core_job(
        _health_snapshot_job,
        "cron",
        hour=HEALTH_SNAPSHOT_HOUR,
        minute=HEALTH_SNAPSHOT_MINUTE,
        id="plant_health_snapshot",
        name="Daily plant health snapshot",
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
        # Loop: on a stopped scheduler pending jobs are not deduped by id.
        while scheduler.get_job(job_id) is not None:
            scheduler.remove_job(job_id)
    _add_tz_bound_cron_jobs(settings)
    if paused:
        apply_persisted_pause(True)


def apply_timezone_preference(request: "Request", tz_name: str | None) -> None:
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

    gateway = _get_cloud()
    if gateway is None:
        logger.debug("Sync job skipped: no Tuya credentials")
        return

    registry = getattr(_app.state, "device_registry", None)  # type: ignore[union-attr]  # None _app escapes as AttributeError (pinned)
    with _job_session(_app, logger, "Sync job failed") as session:
        repo = IrrigationRepository(session)
        sync_svc = SyncService(repo, registry, gateway)
        sync_svc.sync_all_sensors(hours=SYNC_JOB_BACKFILL_HOURS)


def _health_snapshot_job() -> None:
    """Background job: compute and persist daily plant health snapshots."""
    from greenhouse_server.services.health import PlantHealthService

    with _job_session(_app, logger, "Plant health snapshot job failed") as session:
        from greenhouse_core.repository import IrrigationRepository

        repo = IrrigationRepository(session)
        svc = PlantHealthService(repo, _app.state.plant_db)  # type: ignore[union-attr]  # None _app escapes as AttributeError (pinned)
        svc.snapshot_daily()


def _build_irrigation_service(
    app: FastAPI, repo: IrrigationRepository, registry: "DeviceRegistry | None", gateway: DeviceGateway | None
) -> "IrrigationService":
    """Wire the check job's service on the job's own repo; the shared health monitor is re-bound to it first."""
    from greenhouse_server.services.irrigation import IrrigationService
    from greenhouse_server.services.sync import SyncService

    sync_svc = SyncService(repo, registry, gateway)
    monitor = getattr(app.state, "health_monitor", None)
    if monitor is not None:
        monitor.bind_repo(repo)
    return IrrigationService(
        repo=repo,
        registry=registry,
        sync_service=sync_svc,
        weather_client=app.state.weather_client,
        plant_db=app.state.plant_db,
        health_monitor=monitor,
        notifier=getattr(app.state, "ntfy_notifier", None),
    )


def _check_job() -> None:
    """Background job: check all clusters."""
    # Resolved here, before the session opens, exactly as before the extraction: an import
    # failure escapes the job instead of being logged as "Check job failed".
    from greenhouse_server.services.irrigation import IrrigationService  # noqa: F401
    from greenhouse_server.services.sync import SyncService  # noqa: F401

    gateway = _get_cloud()
    registry = getattr(_app.state, "device_registry", None)  # type: ignore[union-attr]  # None _app escapes as AttributeError (pinned)

    with _job_session(_app, logger, "Check job failed") as session:
        repo = IrrigationRepository(session)
        _build_irrigation_service(_app, repo, registry, gateway).check_all_clusters()  # type: ignore[arg-type]  # non-None: _job_session already read _app.state


def _anomaly_job() -> None:
    """Background job: scan all sensors for staleness and drift anomalies."""
    from greenhouse_server.services.anomaly import SensorAnomalyService

    with _job_session(_app, logger, "Anomaly scan job failed") as session:
        repo = IrrigationRepository(session)
        SensorAnomalyService(repo, notifier=getattr(_app.state, "ntfy_notifier", None)).scan()  # type: ignore[union-attr]  # None _app escapes as AttributeError (pinned)


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

    with _job_session(_app, logger, "Device health monitor job failed") as session:
        repo = IrrigationRepository(session)
        monitor.bind_repo(repo)
        monitor.poll_all()


def init_health_monitor(app: FastAPI, settings: Settings) -> None:  # noqa: ARG001 — public signature
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
            monitor.backfill_from_history()
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Health monitor startup hooks failed")
        app.state.health_monitor = monitor
    finally:
        session.close()


def _is_paused(job: "Job") -> bool:
    """True only when ``job`` was explicitly paused.

    APScheduler pauses a job by setting ``next_run_time`` to ``None``. A job
    added before the scheduler starts has *no* ``next_run_time`` attribute at
    all — it is computed on ``start()`` — so "not scheduled yet" must not be
    read as "paused". (Reading it that way reported every job paused on a
    stopped scheduler, and made ``reschedule_for_timezone`` re-pause an
    un-paused ``check_all`` behind the persisted preference's back.)
    """
    return hasattr(job, "next_run_time") and job.next_run_time is None


class JobInfo(TypedDict):
    """One ``get_jobs`` row: a plain dict at runtime (``SchedulerJobResponse(**job)``, web jobs table)."""

    id: str
    name: str
    trigger: str
    next_run_time: str | None
    paused: bool
    core: bool


def get_jobs() -> list[JobInfo]:
    """List all registered jobs.

    ``paused`` is True only for an explicitly paused job (only ``check_all``
    can be paused, and that state mirrors ``user_preferences.scheduler_paused``).
    ``next_run_time`` is the next fire time, or None when the job is paused
    or the scheduler is not running (nothing will fire). Pair with the
    ``scheduler_running`` flag on ``/health`` to tell the two apart.
    """
    running = scheduler.running
    jobs: list[JobInfo] = []
    for job in scheduler.get_jobs():
        next_run = getattr(job, "next_run_time", None) if running else None
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "trigger": str(job.trigger),
                "next_run_time": str(next_run) if next_run else None,
                "paused": _is_paused(job),
                "core": job.id in _CORE_JOB_IDS,
            }
        )
    return jobs


def is_check_all_paused() -> bool:
    """True when the `check_all` job is currently (explicitly) paused."""
    job = scheduler.get_job(CHECK_ALL_JOB_ID)
    if job is None:
        return False
    return _is_paused(job)


def delete_job(job_id: str) -> None:
    """Unregister an ad-hoc job; the single code path for the API and web UI.

    Raises:
        CoreJobError: ``job_id`` is a built-in job (pause ``check_all`` instead).
        JobNotRegisteredError: no job with that id is registered.
    """
    if job_id in _CORE_JOB_IDS:
        raise CoreJobError(
            f"Job {job_id} is a built-in scheduler job and cannot be deleted. "
            "To stop automatic irrigation checks use POST /api/v1/scheduler/pause "
            "(resume with POST /api/v1/scheduler/resume)."
        )
    try:
        scheduler.remove_job(job_id)
    except JobLookupError:
        raise JobNotRegisteredError(f"Job {job_id} not found") from None


def set_check_all_paused(repo: IrrigationRepository, paused: bool) -> bool:
    """Pause or resume `check_all` and persist the flag; shared by API and web UI.

    Works whether or not the scheduler is running: on a stopped scheduler the
    registered (pending) job is paused/resumed and the preference persisted,
    so it takes effect when the scheduler starts and survives restarts.
    Commits because the live scheduler has already changed: the persisted
    flag must match it before the caller returns.

    Args:
        repo: Repository whose session receives the preference write (committed here).
        paused: True to pause, False to resume.

    Returns:
        The resulting paused state of `check_all`.

    Raises:
        JobNotRegisteredError: the `check_all` job is not registered (nothing
            is persisted in that case).
    """
    if scheduler.get_job(CHECK_ALL_JOB_ID) is None:
        raise JobNotRegisteredError(f"Job {CHECK_ALL_JOB_ID} not found")
    if paused:
        scheduler.pause_job(CHECK_ALL_JOB_ID)
    else:
        scheduler.resume_job(CHECK_ALL_JOB_ID)
    repo.update_preferences(scheduler_paused=paused)
    repo.commit()
    return is_check_all_paused()


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
