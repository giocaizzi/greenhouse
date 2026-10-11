"""Pump-watcher job plumbing: schedule the dry-run watcher, run it, and handle a shutdown cut-off.

Background-job code, kept apart from the pipeline in :mod:`.irrigation` (as the leak-check
jobs are in :mod:`.irrigation_jobs`). Every real start — automatic, forced or manual — arms a
watcher (:func:`schedule_pump_watcher`); the job body (:func:`_run_pump_watcher`) opens its own
session and runs :class:`~greenhouse_server.services.pump_watcher.PumpWatcherService`; when the
server shuts down mid-cycle, :func:`handle_watcher_interrupted` decides what happens to the pump.
The scheduler is imported lazily, inside each function, because it sits above the services
layer and reads the app at call time. Records go to the ``greenhouse_server.services.irrigation``
logger, the pipeline's, so log routing and filters keyed on it keep matching.
"""

import functools
import logging
import time as _time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from greenhouse_core.constants import (
    PUMP_WATCHER_MAX_READ_FAILURES,
    PUMP_WATCHER_POLL_SECONDS,
    PUMP_WATCHER_WARMUP_SECONDS,
)
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.models import (
    ENTITY_IRRIGATOR,
    EVENT_ACTION_STOP,
    SOURCE_IRRIGATION,
    TRIGGERED_BY_AUTO,
    TRIGGERED_BY_SHUTDOWN,
    TriggeredBy,
)
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.jobs import job_session

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

    from greenhouse_core.models import Irrigator
    from greenhouse_server.config import Settings

logger = logging.getLogger("greenhouse_server.services.irrigation")

WATCHER_SHUTDOWN_ACTIVITY_CODE = "pump_watcher_shutdown"


def _stop_auto_cycle(
    repo: IrrigationRepository, registry: DeviceRegistry, irrigator: "Irrigator", *, kind: str = "auto"
) -> tuple[bool, str]:
    """Best-effort stop of a server-timed cycle at shutdown; returns ``(stop_ok, activity message)``.

    ``kind`` names the cycle in the logs and the activity text: ``auto``, or ``forced`` for a
    forced ``/irrigate`` run (recorded as ``manual``, but timed by the engine).
    """
    stop_ok = False
    stop_msg = ""
    try:
        stop_ok, stop_msg = registry.get_irrigator(irrigator).stop(irrigator)
    except Exception as exc:  # noqa: BLE001 — best-effort during shutdown
        stop_msg = f"adapter.stop raised: {exc}"
    if stop_ok:
        logger.warning(
            "Server shutting down mid-irrigation: stopped %s cycle on irrigator %d "
            "(dry-run watcher can no longer protect it)",
            kind,
            irrigator.id,
        )
        repo.add_irrigation_event(
            irrigator_id=irrigator.id,
            action=EVENT_ACTION_STOP,
            triggered_by=TRIGGERED_BY_SHUTDOWN,
            notes=f"server shutdown: dry-run watcher interrupted, {kind} cycle stopped",
            timestamp=int(_time.time()),
        )
        return stop_ok, f"Server shutdown stopped the {kind} irrigation on '{irrigator.name}' (watcher interrupted)"
    logger.error(
        "Server shutting down mid-irrigation: FAILED to stop %s cycle on irrigator %d (%s) — "
        "it continues unprotected until the device timer ends it",
        kind,
        irrigator.id,
        stop_msg,
    )
    return stop_ok, (
        f"Server shutdown could not stop the {kind} irrigation on '{irrigator.name}' ({stop_msg}); "
        "it continues without dry-run protection"
    )


def _left_running_message(irrigator: "Irrigator", triggered_by: TriggeredBy) -> str:
    """Warn that a non-auto cycle keeps running unwatched; returns the activity message."""
    logger.warning(
        "Server shutting down mid-irrigation: %s cycle on irrigator %d left running "
        "unprotected (no dry-run watcher) until the device timer ends it",
        triggered_by,
        irrigator.id,
    )
    return (
        f"Server shutdown: {triggered_by} irrigation on '{irrigator.name}' continues "
        "without dry-run protection until the device timer ends it"
    )


def handle_watcher_interrupted(
    repo: IrrigationRepository,
    registry: DeviceRegistry,
    irrigator: "Irrigator",
    *,
    triggered_by: TriggeredBy,
    started_at: int,
    forced: bool = False,
) -> bool:
    """Shutdown policy for a pump whose dry-run watcher was cut short.

    The server is going down mid-irrigation, so nothing will watch DP 105 for
    the rest of the cycle. The device's own duration timer (DP 102) still
    bounds the run either way; what changes is dry-run protection:

    * ``auto`` cycles are **stopped** — the server started them, and leaving
      a pump running unwatched trades a short watering (safe; the engine
      re-evaluates after restart) for possible dry-run damage.
    * ``forced`` runs (``/irrigate`` with ``force``, recorded as ``manual``) are
      **stopped** too: the engine chose the duration and nobody is watching.
    * ``manual`` starts are **left running**: the user explicitly asked for
      that duration, and the watcher's own precedent for losing its signal
      ("abandoned" after read failures) is to warn, not stop. Logged loudly.

    Best-effort: a failed stop is logged, never raised.

    Args:
        repo: Repository bound to the watcher job's session (caller commits).
        registry: Device registry resolving the irrigator's adapter.
        irrigator: The irrigator whose watcher was interrupted.
        triggered_by: ``"auto"`` or ``"manual"`` — who started the cycle.
        started_at: Unix timestamp of the start event.
        forced: The cycle is a forced pipeline run (stopped like an auto one).

    Returns:
        True if the pump was stopped successfully, False otherwise.
    """
    stop_ok = False
    if triggered_by == TRIGGERED_BY_AUTO or forced:
        stop_ok, message = _stop_auto_cycle(repo, registry, irrigator, kind="forced" if forced else "auto")
    else:
        message = _left_running_message(irrigator, triggered_by)
    try:
        repo.add_activity_event(
            source=SOURCE_IRRIGATION,
            entity_type=ENTITY_IRRIGATOR,
            entity_id=irrigator.id,
            code=WATCHER_SHUTDOWN_ACTIVITY_CODE,
            message=message,
            severity="warning",
            payload={"triggered_by": triggered_by, "started_at": started_at, "stopped": stop_ok}
            | ({"forced": True} if forced else {}),
        )
    except Exception:
        logger.exception("Failed to record watcher-shutdown activity for irrigator %d", irrigator.id)
    return stop_ok


def _watcher_tuning(settings: "Settings | None") -> tuple[float, float, int]:
    """Poll interval, warm-up and read-failure budget for a watcher; constants when settings are absent."""
    if settings is None:
        return PUMP_WATCHER_POLL_SECONDS, PUMP_WATCHER_WARMUP_SECONDS, PUMP_WATCHER_MAX_READ_FAILURES
    return (
        settings.pump_watcher_poll_seconds,
        settings.pump_watcher_warmup_seconds,
        settings.pump_watcher_max_read_failures,
    )


def _run_pump_watcher(
    app: "FastAPI",
    registry: DeviceRegistry,
    *,
    irrigator_id: int,
    duration_seconds: int,
    started_at: int,
    triggered_by: TriggeredBy,
    sleep: "Callable[[float], bool]",
    stop_requested: "Callable[[], bool]",
    forced: bool = False,
) -> None:
    """The watcher job body: its own session; tuning and the health monitor are read when it runs."""
    from greenhouse_server.services.pump_watcher import PumpWatcherService

    with job_session(app, logger, "Pump watcher job failed for irrigator %d", irrigator_id, commit=False) as session:
        repo = IrrigationRepository(session)
        irrigator = repo.get_irrigator(irrigator_id)
        if irrigator is None:
            return
        poll, warmup, max_failures = _watcher_tuning(getattr(app.state, "settings", None))
        shared = getattr(app.state, "health_monitor", None)
        # The watcher's own view: its alerts go into this job's session, never another job's (S6).
        monitor = shared.for_repo(repo) if shared is not None else None
        watcher = PumpWatcherService(
            repo,
            registry,
            poll_seconds=poll,
            warmup_seconds=warmup,
            max_read_failures=max_failures,
            monitor=monitor,
            sleep=sleep,
            stop_requested=stop_requested,
        )
        result = watcher.watch(irrigator, duration_seconds, started_at=started_at)
        if result["outcome"] == "interrupted":
            handle_watcher_interrupted(
                repo, registry, irrigator, triggered_by=triggered_by, started_at=started_at, forced=forced
            )
            session.commit()


def schedule_pump_watcher(
    irrigator_id: int,
    duration_minutes: int,
    started_at: int,
    *,
    triggered_by: TriggeredBy = TRIGGERED_BY_AUTO,
    forced: bool = False,
) -> bool:
    """Schedule a dry-run watcher to run for the duration of an irrigation.

    Spawns a one-shot APScheduler ``date`` job that opens its own DB session,
    instantiates ``PumpWatcherService``, and polls DP 105 until the cycle is
    over or a dry-run trip fires. Skips silently when the scheduler isn't
    running (test environments) or when the feature is disabled in settings.

    Args:
        irrigator_id: Irrigator to watch.
        duration_minutes: Requested irrigation duration; watcher exits at the
            same wall-clock as the device's auto-off timer.
        started_at: Unix timestamp of the start event (recorded in the
            aborted-event row if the watcher trips).
        triggered_by: Who started the cycle (``"auto"`` / ``"manual"``);
            decides what server shutdown does to the still-running pump —
            see :func:`handle_watcher_interrupted`.
        forced: A forced pipeline run: stopped at shutdown like an auto cycle.

    Returns:
        True if a job was scheduled, False if the scheduler is unavailable,
        the watcher is disabled, the irrigator has no device registry, or
        the duration is non-positive.
    """
    if duration_minutes <= 0:
        return False
    try:
        from greenhouse_server.scheduler import _app, scheduler, shutdown_requested, wait_for_shutdown

        if not scheduler.running or _app is None:
            return False
        settings: Settings | None = getattr(_app.state, "settings", None)
        if settings is not None and not settings.pump_watcher_enabled:
            return False
        registry: DeviceRegistry | None = getattr(_app.state, "device_registry", None)
        if registry is None:
            return False
        run_date = datetime.fromtimestamp(started_at, tz=UTC)

        run = functools.partial(
            _run_pump_watcher,
            _app,
            registry,
            irrigator_id=irrigator_id,
            duration_seconds=int(duration_minutes * 60),
            started_at=started_at,
            triggered_by=triggered_by,
            sleep=wait_for_shutdown,
            stop_requested=shutdown_requested,
            forced=forced,
        )
        scheduler.add_job(
            run,
            "date",
            run_date=run_date,
            id=f"pump-watcher-{irrigator_id}-{started_at}",
            name=f"Pump watcher irrigator {irrigator_id}",
            replace_existing=True,
        )
        return True
    except Exception:
        logger.debug("Could not schedule pump watcher for irrigator %d", irrigator_id, exc_info=True)
        return False
