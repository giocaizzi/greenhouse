"""Irrigation and monitoring orchestration."""

import json
import logging
import time as _time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Required, TypedDict, cast

from greenhouse_core.constants import (
    FALLBACK_TEMPERATURE_C,
    LEAK_CHECK_ACTIVITY_SCAN_LIMIT,
    LEAK_CHECK_DELAY_SECONDS,
    LEAK_HOLD_HOURS,
    MONITOR_LOOKBACK_HOURS,
    MONITOR_VERY_DRY_MARGIN,
    MONITOR_WET_MARGIN,
    PUMP_WATCHER_MAX_READ_FAILURES,
    PUMP_WATCHER_POLL_SECONDS,
    PUMP_WATCHER_WARMUP_SECONDS,
)
from greenhouse_core.devices import DeviceRegistry, UnknownDeviceModel
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.logic.cleaning import clean_readings_desc
from greenhouse_core.logic.decision import Action, Severity
from greenhouse_core.logic.plant_needs import moisture_target_range
from greenhouse_core.models import ENTITY_CLUSTER, ENTITY_IRRIGATOR
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import SOURCE_LEAK, raise_alert, sync_cluster_alerts
from greenhouse_server.services.health_monitor import HEALTH_ALARM_TO_TRIGGER, DeviceHealthMonitor
from greenhouse_server.services.maintenance import collect_learning_alerts, collect_maintenance_alerts
from greenhouse_server.services.notify import NtfyClient, maybe_notify
from greenhouse_server.services.sync import SyncService
from greenhouse_server.services.weather import WeatherClient

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from sqlalchemy.orm import Session

    from greenhouse_core.devices import AbstractIrrigatorAdapter
    from greenhouse_core.logic.cleaning import CleanedReading
    from greenhouse_core.logic.decision import IrrigationDecision
    from greenhouse_core.models import Irrigator
    from greenhouse_server.config import Settings

logger = logging.getLogger(__name__)

CHECK_FAILED_ALERT_CODE = "check_failed"


WATCHER_SHUTDOWN_ACTIVITY_CODE = "pump_watcher_shutdown"


def _stop_auto_cycle(repo: IrrigationRepository, registry: DeviceRegistry, irrigator: "Irrigator") -> tuple[bool, str]:
    """Best-effort stop of an auto cycle at shutdown; returns ``(stop_ok, activity message)``."""
    stop_ok = False
    stop_msg = ""
    try:
        stop_ok, stop_msg = registry.get_irrigator(irrigator).stop(irrigator)
    except Exception as exc:  # noqa: BLE001 — best-effort during shutdown
        stop_msg = f"adapter.stop raised: {exc}"
    if stop_ok:
        logger.warning(
            "Server shutting down mid-irrigation: stopped auto cycle on irrigator %d "
            "(dry-run watcher can no longer protect it)",
            irrigator.id,
        )
        repo.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="stop",
            triggered_by="shutdown",
            notes="server shutdown: dry-run watcher interrupted, auto cycle stopped",
            timestamp=int(_time.time()),
        )
        return stop_ok, f"Server shutdown stopped the auto irrigation on '{irrigator.name}' (watcher interrupted)"
    logger.error(
        "Server shutting down mid-irrigation: FAILED to stop auto cycle on irrigator %d (%s) — "
        "it continues unprotected until the device timer ends it",
        irrigator.id,
        stop_msg,
    )
    return stop_ok, (
        f"Server shutdown could not stop the auto irrigation on '{irrigator.name}' ({stop_msg}); "
        "it continues without dry-run protection"
    )


def _left_running_message(irrigator: "Irrigator", triggered_by: str) -> str:
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
    triggered_by: str,
    started_at: int,
) -> bool:
    """Shutdown policy for a pump whose dry-run watcher was cut short.

    The server is going down mid-irrigation, so nothing will watch DP 105 for
    the rest of the cycle. The device's own duration timer (DP 102) still
    bounds the run either way; what changes is dry-run protection:

    * ``auto`` cycles are **stopped** — the server started them, and leaving
      a pump running unwatched trades a short watering (safe; the engine
      re-evaluates after restart) for possible dry-run damage.
    * ``manual`` cycles are **left running**: the user explicitly asked for
      that duration, and the watcher's own precedent for losing its signal
      ("abandoned" after read failures) is to warn, not stop. Logged loudly.

    Best-effort: a failed stop is logged, never raised.

    Args:
        repo: Repository bound to the watcher job's session (caller commits).
        registry: Device registry resolving the irrigator's adapter.
        irrigator: The irrigator whose watcher was interrupted.
        triggered_by: ``"auto"`` or ``"manual"`` — who started the cycle.
        started_at: Unix timestamp of the start event.

    Returns:
        True if the pump was stopped successfully, False otherwise.
    """
    stop_ok = False
    if triggered_by == "auto":
        stop_ok, message = _stop_auto_cycle(repo, registry, irrigator)
    else:
        message = _left_running_message(irrigator, triggered_by)
    try:
        repo.add_activity_event(
            source="irrigation",
            entity_type=ENTITY_IRRIGATOR,
            entity_id=irrigator.id,
            code=WATCHER_SHUTDOWN_ACTIVITY_CODE,
            message=message,
            severity="warning",
            payload={"triggered_by": triggered_by, "started_at": started_at, "stopped": stop_ok},
        )
    except Exception:  # noqa: BLE001
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
    app: Any,
    registry: DeviceRegistry,
    *,
    irrigator_id: int,
    duration_seconds: int,
    started_at: int,
    triggered_by: str,
    sleep: "Callable[[float], bool]",
    stop_requested: "Callable[[], bool]",
) -> None:
    """The watcher job body: its own session; tuning and the health monitor are read when it runs."""
    from greenhouse_core.repository import IrrigationRepository
    from greenhouse_server.services.pump_watcher import PumpWatcherService

    session = app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        irrigator = repo.get_irrigator(irrigator_id)
        if irrigator is None:
            return
        poll, warmup, max_failures = _watcher_tuning(getattr(app.state, "settings", None))
        monitor = getattr(app.state, "health_monitor", None)
        if monitor is not None:
            monitor.bind_repo(repo)
        watcher = PumpWatcherService(
            repo,
            registry,
            poll_seconds=poll,
            warmup_seconds=warmup,
            max_read_failures=max_failures,
            monitor=monitor,
            sleep=sleep,  # type: ignore[arg-type]  # contract: pump_watcher.py (WP4) types sleep -> None; the bool is ignored
            stop_requested=stop_requested,
        )
        result = watcher.watch(irrigator, duration_seconds, started_at=started_at)
        if result["outcome"] == "interrupted":
            handle_watcher_interrupted(repo, registry, irrigator, triggered_by=triggered_by, started_at=started_at)
            session.commit()
    except Exception:
        session.rollback()
        logger.exception("Pump watcher job failed for irrigator %d", irrigator_id)
    finally:
        session.close()


def schedule_pump_watcher(
    irrigator_id: int, duration_minutes: int, started_at: int, *, triggered_by: str = "auto"
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
        duration_seconds = int(duration_minutes * 60)

        def _run() -> None:
            _run_pump_watcher(
                _app,
                registry,
                irrigator_id=irrigator_id,
                duration_seconds=duration_seconds,
                started_at=started_at,
                triggered_by=triggered_by,
                sleep=wait_for_shutdown,
                stop_requested=shutdown_requested,
            )

        scheduler.add_job(
            _run,
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


LEAK_CHECK_ACTIVITY_CODE = "leak_check"
# Activity codes that prove a start's leak check already completed:
# ``leak_check`` is written by every completed check; ``leak_hold`` is
# written when a check raised a hold (and predates the ``leak_check`` marker).
_LEAK_CHECK_DONE_CODES = frozenset({LEAK_CHECK_ACTIVITY_CODE, "leak_hold"})


def _leak_check_done(repo: IrrigationRepository, cluster_id: int, started_at: int) -> bool:
    """True when the leak check for this (cluster, start) has already completed.

    There is no dedicated table for leak-check runs; the activity log is the
    durable record. Every completed check writes a ``leak_check`` row (see
    :func:`_run_leak_check`), and a check that raised a hold also wrote a
    ``leak_hold`` row — both carry ``started_at`` in their payload.
    """
    for event in repo.list_activity_events(
        entity_type=ENTITY_CLUSTER, entity_id=cluster_id, source=SOURCE_LEAK, limit=LEAK_CHECK_ACTIVITY_SCAN_LIMIT
    ):
        if event.code not in _LEAK_CHECK_DONE_CODES or not event.payload_json:
            continue
        try:
            if json.loads(event.payload_json).get("started_at") == started_at:
                return True
        except (ValueError, AttributeError):
            continue
    return False


def _run_leak_check(cluster_id: int, started_at: int) -> None:
    """Scheduler job: run the post-irrigation leak check once, then mark it done.

    Idempotent: skips when the check for this start already completed (a
    normal job and a restart re-arm can both exist for the same start). The
    ``leak_check`` marker is committed in the same transaction as the check's
    own effects, so a failed check leaves no marker and is re-armed on the
    next startup.
    """
    from greenhouse_server.scheduler import _app
    from greenhouse_server.services.leak import LeakDetectionService

    if _app is None:
        return
    session = _app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        if _leak_check_done(repo, cluster_id, started_at):
            logger.debug("Leak check for cluster %d start %d already done", cluster_id, started_at)
            return
        alerts = LeakDetectionService(
            repo, _app.state.plant_db, notifier=getattr(_app.state, "ntfy_notifier", None)
        ).check_after_irrigation(cluster_id, started_at)
        repo.add_activity_event(
            source=SOURCE_LEAK,
            entity_type=ENTITY_CLUSTER,
            entity_id=cluster_id,
            code=LEAK_CHECK_ACTIVITY_CODE,
            message=(
                f"post-irrigation leak check: {len(alerts)} sensor(s) flagged"
                if alerts
                else "post-irrigation leak check: no leak found"
            ),
            severity="info",
            payload={"started_at": started_at, "flagged_sensor_ids": [a.entity_id for a in alerts]},
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Leak check job failed for cluster %d", cluster_id)
    finally:
        session.close()


def _add_leak_check_job(cluster_id: int, started_at: int, *, run_at: int | None = None) -> None:
    from greenhouse_server.scheduler import scheduler

    due = started_at + LEAK_CHECK_DELAY_SECONDS
    scheduler.add_job(
        _run_leak_check,
        "date",
        run_date=datetime.fromtimestamp(run_at if run_at is not None else due, tz=UTC),
        args=[cluster_id, started_at],
        id=f"leak-check-{cluster_id}-{started_at}",
        name=f"Leak check cluster {cluster_id}",
        replace_existing=True,
    )


def _schedule_leak_check(cluster_id: int, started_at: int) -> None:
    """Schedule a one-shot leak detection check 30 minutes after an irrigation start.

    Skips silently when the scheduler is not running (test environments).
    Tests should call ``LeakDetectionService.check_after_irrigation`` directly.
    The job is in-memory; :func:`rearm_leak_checks` restores it after a restart.
    """
    try:
        from greenhouse_server.scheduler import scheduler

        if not scheduler.running:
            return
        _add_leak_check_job(cluster_id, started_at)
    except Exception:
        # Scheduling must never block irrigation
        logger.debug("Could not schedule leak check for cluster %d", cluster_id, exc_info=True)


def _rearm_from_events(repo: IrrigationRepository, now: int) -> "Iterator[None]":
    """Schedule a leak check for every recent auto start whose check never completed; yield once per job.

    A generator rather than a returned count, so the caller's tally keeps the jobs already added
    when the scan fails midway (pinned by the partial-count characterization test).
    """
    for irrigator in repo.list_all_irrigators():
        for event in repo.get_recent_events(irrigator.id, hours=LEAK_HOLD_HOURS):
            if event.action != "start" or event.triggered_by != "auto":
                continue
            if _leak_check_done(repo, irrigator.cluster_id, event.timestamp):
                continue
            due = event.timestamp + LEAK_CHECK_DELAY_SECONDS
            _add_leak_check_job(irrigator.cluster_id, event.timestamp, run_at=max(due, now))
            yield


def rearm_leak_checks() -> int:
    """Re-schedule leak checks lost to a restart; call once after the scheduler starts.

    Leak-check jobs live only in memory, so a restart inside the 30-min window
    after an auto start used to drop the check — and with it the leak hold.
    Scans auto ``start`` events from the last ``LEAK_HOLD_HOURS`` (older
    findings would be outside the hold horizon) and schedules every one whose
    check has not completed (see :func:`_leak_check_done`): at its normal due
    time if that is still ahead, otherwise immediately. Manual starts never
    get a leak check, so they are not re-armed. Idempotent across restarts.

    Returns:
        Number of leak checks scheduled (0 when the scheduler isn't running).
    """
    from greenhouse_server.scheduler import _app, scheduler

    if not scheduler.running or _app is None:
        return 0
    now = int(_time.time())
    scheduled = 0
    session = _app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        for _ in _rearm_from_events(repo, now):
            scheduled += 1
    except Exception:
        logger.exception("Re-arming leak checks after restart failed")
    finally:
        session.close()
    if scheduled:
        logger.info("Re-armed %d post-irrigation leak check(s) after restart", scheduled)
    return scheduled


class PipelineResult(TypedDict, total=False):
    """``run_irrigation_pipeline`` result: a plain dict at runtime (``IrrigateResponse(**result)``, decision panel)."""

    action: Required[str]
    reason: Required[str]
    confidence: Required[float]
    duration_minutes: int
    interval_hours: int
    stress_indicators: dict[str, Any]
    reasons: list[dict[str, Any]]
    temperature: float
    temperature_source: str
    blocking_alarms: list[str]


class MonitorResult(TypedDict):
    """``monitor_cluster`` result: a plain dict at runtime (``MonitorResponse``)."""

    cluster_name: str
    sensors: list[dict[str, Any]]
    needs_water: list[str]


class CheckResult(TypedDict, total=False):
    """One ``check_cluster`` entry: a plain dict at runtime; ``notes`` or ``needs_water`` per branch."""

    cluster_id: Required[int]
    cluster_name: Required[str]
    action: Required[str]
    notes: str
    needs_water: list[str]
    alerts: list[dict[str, Any]]
    maintenance: list[dict[str, Any]]


def _error_result(reason: str) -> PipelineResult:
    """The pipeline's early-exit shape (``routes/operations`` string-matches "cluster not found")."""
    return {"action": "error", "reason": reason, "confidence": 0}


def _decision_result(decision: "IrrigationDecision", temp: float, source: str) -> PipelineResult:
    """The pipeline result for an evaluated decision, before any actuation outcome is applied."""
    return {
        "action": decision.action.value,
        "reason": decision.reason_text,
        "confidence": decision.confidence,
        "duration_minutes": decision.duration_minutes,
        "interval_hours": decision.interval_hours,
        "stress_indicators": decision.stress_indicators.model_dump(exclude_none=True),
        "reasons": [r.model_dump() for r in decision.reasons],
        "temperature": temp,
        "temperature_source": source,
    }


def _with_error(result: PipelineResult, reason: str) -> PipelineResult:
    """Turn ``result`` into an error in place (key positions unchanged) and return the same dict."""
    result["action"] = "error"
    result["reason"] = reason
    return result


@dataclass(frozen=True, slots=True)
class _Actuation:
    """Everything the actuation half of the pipeline needs, resolved before the pump is started."""

    cluster_id: int
    irrigator: "Irrigator"
    adapter: "AbstractIrrigatorAdapter"
    decision: "IrrigationDecision"
    temp: float
    source: str
    sensor_data: "dict[str, Any] | None"


def _soil_note(sensor_data: "dict[str, Any] | None") -> str:
    """The event-notes soil fragment, labelled as the cluster's driest sensor (invariant #2)."""
    # The snapshot's soil value is the cluster's driest sensor (invariant #2),
    # so label it as such — an unqualified "soil=" reads as "this plant's".
    return (
        f", soil={sensor_data['soil_moisture']:.0f}% (driest)"
        if sensor_data and sensor_data.get("soil_moisture") is not None
        else ""
    )


def _event_notes(act: _Actuation, soil_note: str) -> str:
    """The ``notes`` text of the start / attempted irrigation event."""
    return (
        f"temp={act.temp:.1f}C ({act.source}){soil_note}, "
        f"confidence={act.decision.confidence:.0%}, reason={act.decision.reason_text}"
    )


def _latest_soil(readings: "Sequence[CleanedReading]") -> float | None:
    """Newest non-null soil value of a newest-first (cleaned) series, or None."""
    return next((r.soil_moisture for r in readings if r.soil_moisture is not None), None) if readings else None


def _soil_status(latest_soil: float | None, t_min: float, t_max: float) -> str:
    """Classify one sensor's latest soil value against the target band."""
    if latest_soil is None:
        return "no_data"
    if latest_soil < t_min - MONITOR_VERY_DRY_MARGIN:
        return "very_dry"
    if latest_soil < t_min:
        return "dry"
    if latest_soil > t_max + MONITOR_WET_MARGIN:
        return "wet"
    return "ok"


def _check_result(
    cluster_id: int,
    cluster_name: str,
    action: str,
    *,
    detail_key: Literal["notes", "needs_water"],
    detail: str | list[str],
    alerts: list[dict[str, Any]],
    maintenance: list[dict[str, Any]],
) -> CheckResult:
    """One ``check_cluster`` entry, keys in the response order; ``detail_key`` names the branch's detail."""
    # A TypedDict literal cannot carry a computed key; the runtime object is the same plain dict.
    return cast(
        CheckResult,
        {
            "cluster_id": cluster_id,
            "cluster_name": cluster_name,
            "action": action,
            detail_key: detail,
            "alerts": alerts,
            "maintenance": maintenance,
        },
    )


class IrrigationService:
    """Orchestrates irrigation decisions, execution, and monitoring."""

    def __init__(
        self,
        repo: IrrigationRepository,
        registry: DeviceRegistry | None,
        sync_service: SyncService,
        weather_client: WeatherClient,
        plant_db: PlantDatabase,
        health_monitor: DeviceHealthMonitor | None = None,
        notifier: NtfyClient | None = None,
    ):
        self._repo = repo
        self._registry = registry
        self._sync = sync_service
        self._weather = weather_client
        self._plant_db = plant_db
        self._health_monitor = health_monitor
        self._notifier = notifier

    def _resolve_temperature(
        self,
        cluster_id: int,
        is_indoor: bool,
        temp_override: float | None,
        no_sync: bool,
    ) -> tuple[float, str, dict[str, Any] | None]:
        """Resolve temperature from override, sensor, or weather. Returns (temp, source, sensor_data)."""
        if temp_override is not None:
            return temp_override, "override", None

        sensor_data = None if no_sync else self._sync.ensure_fresh_and_read(cluster_id)
        picked = self._indoor_temperature(sensor_data) if is_indoor else self._outdoor_temperature(sensor_data)
        if picked is not None:
            temp, source = picked
            return temp, source, sensor_data
        return FALLBACK_TEMPERATURE_C, "fallback (20C)", sensor_data

    def _indoor_temperature(self, sensor_data: dict[str, Any] | None) -> tuple[float, str] | None:
        """Indoor: the cluster's own sensor first, then the weather feels-like; None if neither."""
        if sensor_data and sensor_data.get("temperature") is not None:
            return sensor_data["temperature"], "sensor"
        weather = self._weather.get_current()
        if weather and weather.get("feels_like") is not None:
            return weather["feels_like"], "open-meteo (fallback)"
        return None

    def _outdoor_temperature(self, sensor_data: dict[str, Any] | None) -> tuple[float, str] | None:
        """Any non-indoor environment: the weather feels-like first, then the sensor; None if neither."""
        weather = self._weather.get_current()
        if weather and weather.get("feels_like") is not None:
            return weather["feels_like"], "open-meteo"
        if sensor_data and sensor_data.get("temperature") is not None:
            return sensor_data["temperature"], "sensor (weather unavailable)"
        return None

    def _decide(self, cluster_id: int, temp: float, *, force: bool) -> "IrrigationDecision | None":
        """Run (and persist) the engine; ``force`` records a manual trigger and bypasses quiet hours."""
        logic = IrrigationLogic(self._repo, self._plant_db, weather_client=self._weather)
        return logic.decide_for_cluster(
            cluster_id,
            current_temp=temp,
            persist=True,
            triggered_by="manual" if force else "auto",
            bypass_quiet_hours=force,
        )

    def _log_decision_skip(self, cluster_id: int, decision: "IrrigationDecision") -> None:
        """Record an automatic skip in the activity log (no payload, unlike the health-gate skip)."""
        self._repo.add_activity_event(
            source="irrigation",
            entity_type=ENTITY_CLUSTER,
            entity_id=cluster_id,
            code="decision_skip",
            message=decision.reason_text,
            severity="info",
        )

    def _actuation_target(self, cluster_id: int) -> "tuple[Irrigator, AbstractIrrigatorAdapter] | str":
        """The cluster's irrigator and its adapter, or the error reason that stops actuation."""
        irrigator = self._repo.get_irrigator_for_cluster(cluster_id)
        if not irrigator:
            return "no irrigators found"
        if self._registry is None:
            return "no device registry"

        try:
            adapter = self._registry.get_irrigator(irrigator)
        except UnknownDeviceModel as exc:
            return f"no adapter for irrigator: {exc}"
        return irrigator, adapter

    def _actuate(self, act: _Actuation, result: PipelineResult) -> PipelineResult:
        """Start the pump, record the event, then apply the started / failed follow-ups to ``result``."""
        duration = act.decision.duration_minutes
        success, output = act.adapter.start(act.irrigator, duration)
        soil_note = _soil_note(act.sensor_data)
        # One timestamp for the event row, the leak check and the watcher, so
        # the restart re-arm (which reads the row) can match the leak check.
        started_at = int(_time.time())
        self._repo.add_irrigation_event(
            irrigator_id=act.irrigator.id,
            action="start" if success else "attempted",
            duration_minutes=duration,
            triggered_by="auto",
            timestamp=started_at,
            notes=_event_notes(act, soil_note),
        )

        if success:
            self._on_started(act, started_at)
            result["action"] = "irrigated"
            self._notify_auto_irrigation(act)
        else:
            self._on_start_failed(act, output)
            _with_error(result, f"irrigator failed: {output}")

        return result

    def _on_started(self, act: _Actuation, started_at: int) -> None:
        """Log the run, flag the decision as actuated, and arm the leak check and dry-run watcher."""
        duration = act.decision.duration_minutes
        self._repo.add_activity_event(
            source="irrigation",
            entity_type=ENTITY_CLUSTER,
            entity_id=act.cluster_id,
            code="irrigated",
            message=f"irrigated for {duration}min (confidence={act.decision.confidence:.0%})",
            severity="info",
            payload={
                "irrigator_id": act.irrigator.id,
                "duration_minutes": duration,
                "confidence": act.decision.confidence,
            },
        )
        if act.decision.decision_log_id is not None:
            self._repo.set_decision_actuated(act.decision.decision_log_id)
        _schedule_leak_check(act.cluster_id, started_at)
        schedule_pump_watcher(act.irrigator.id, duration, started_at)

    def _notify_auto_irrigation(self, act: _Actuation) -> None:
        """Push the auto-irrigation notice (``get_preferences`` still runs as an argument: it may insert)."""
        duration = act.decision.duration_minutes
        maybe_notify(
            self._notifier,
            self._repo.get_preferences(),
            "auto",
            lambda: self._notifier.notify_irrigation(  # type: ignore[union-attr]  # maybe_notify returns first when notifier is None
                triggered_by="auto",
                irrigator_name=act.irrigator.name,
                duration_minutes=duration,
                detail=f"confidence={act.decision.confidence:.0%}",
            ),
        )

    def _on_start_failed(self, act: _Actuation, output: str) -> None:
        """Log the failed start and raise the actuation-failed alert."""
        self._repo.add_activity_event(
            source="irrigation",
            entity_type=ENTITY_CLUSTER,
            entity_id=act.cluster_id,
            code="actuation_failed",
            message=f"irrigator failed: {output}",
            severity="warning",
        )
        raise_alert(
            self._repo,
            source="irrigation",
            code="actuation_failed",
            title="Irrigation Actuation Failed",
            message=f"Irrigator '{act.irrigator.name}' failed to start: {output}",
            severity="warning",
            cluster_id=act.cluster_id,
            notifier=self._notifier,
        )

    def run_irrigation_pipeline(
        self,
        cluster_id: int,
        temp_override: float | None = None,
        dry_run: bool = False,
        no_sync: bool = False,
        force: bool = False,
    ) -> PipelineResult:
        """Full pipeline: sync -> weather -> decide -> execute. Returns result dict.

        ``force=True`` bypasses the quiet-hours gate inside the decision
        engine; a ``MANUAL_OVERRIDE_QUIET_HOURS`` warning Reason is still
        attached to the resulting decision so the audit log records that
        the user pushed past the deny window.
        """
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return _error_result("cluster not found")

        is_indoor = cluster.environment == "indoor"
        temp, source, sensor_data = self._resolve_temperature(cluster_id, is_indoor, temp_override, no_sync)

        decision = self._decide(cluster_id, temp, force=force)
        if not decision:
            return _error_result("no data for decision")

        result = _decision_result(decision, temp, source)

        if dry_run or decision.action.value == "skip":
            if not dry_run:
                self._log_decision_skip(cluster_id, decision)
            return result

        # Execute
        target = self._actuation_target(cluster_id)
        if isinstance(target, str):
            return _with_error(result, target)
        irrigator, adapter = target

        # Device-health gate: if a NO_WATER / RAIN / OFFLINE alarm is open
        # for this irrigator, append a typed Reason and short-circuit to
        # Action.SKIP. Same audit-trail path as the engine's existing skip
        # flow — the decision is re-persisted so decision_logs reflects the
        # block, then the run is treated as a skip.
        if self._health_monitor is not None:
            blocked, blocking_alarms = self._health_monitor.is_actuation_blocked(irrigator)
            if blocked:
                primary = blocking_alarms[0]
                trigger = HEALTH_ALARM_TO_TRIGGER[primary]
                decision.add_reason(
                    code=trigger,
                    message=(f"Actuation blocked by device health: {primary.value} on '{irrigator.name}'"),
                    severity=Severity.CRITICAL,
                )
                decision.action = Action.SKIP
                self._repo.add_activity_event(
                    source="irrigation",
                    entity_type=ENTITY_CLUSTER,
                    entity_id=cluster_id,
                    code="decision_skip",
                    message=decision.reason_text,
                    severity="warning",
                    payload={
                        "blocking_alarms": [a.value for a in blocking_alarms],
                        "irrigator_id": irrigator.id,
                    },
                )
                result["action"] = "skip"
                result["reason"] = decision.reason_text
                result["reasons"] = [r.model_dump() for r in decision.reasons]
                result["blocking_alarms"] = [a.value for a in blocking_alarms]
                return result

        return self._actuate(_Actuation(cluster_id, irrigator, adapter, decision, temp, source, sensor_data), result)

    def monitor_cluster(self, cluster_id: int, no_sync: bool = False) -> MonitorResult:
        """Monitor sensor-only cluster. Returns per-sensor soil status."""
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return {"cluster_name": "unknown", "sensors": [], "needs_water": []}

        if not no_sync:
            self._sync.ensure_fresh_and_read(cluster_id)

        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        plants_by_id = {p.id: p for p in self._repo.get_plants_in_cluster(cluster_id)}

        sensor_statuses: list[dict[str, Any]] = []
        needs_water: list[str] = []

        for sensor in sensors:
            # Cleaned view: monitor classifies each sensor as dry/ok/wet, and a
            # sensor-only cluster has no engine to sanity-check that call.
            readings = clean_readings_desc(self._repo.get_recent_readings(sensor.id, hours=MONITOR_LOOKBACK_HOURS))
            latest_soil = _latest_soil(readings)

            plant = plants_by_id.get(sensor.plant_id) if sensor.plant_id else None
            care = self._plant_db.get_care_data(species=plant.species if plant else None)
            t_min, t_max = moisture_target_range(care)
            status = _soil_status(latest_soil, t_min, t_max)

            sensor_statuses.append(
                {
                    "sensor_id": sensor.id,
                    "sensor_name": sensor.name,
                    "plant_species": plant.species if plant else None,
                    "soil_moisture": latest_soil,
                    "status": status,
                    "target_min": t_min,
                    "target_max": t_max,
                }
            )

            if status in ("very_dry", "dry"):
                needs_water.append(f"{sensor.name} ({plant.species if plant else 'unknown'}): {latest_soil:.0f}%")

        return {"cluster_name": cluster.name, "sensors": sensor_statuses, "needs_water": needs_water}

    def check_cluster(self, cluster_id: int) -> CheckResult:
        """Check a single cluster: irrigate if has irrigators, monitor otherwise."""
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return {"cluster_id": cluster_id, "cluster_name": "unknown", "action": "error", "notes": "not found"}

        irrigator = self._repo.get_irrigator_for_cluster(cluster_id)
        alerts = collect_learning_alerts(self._repo, cluster_id, self._plant_db)
        maintenance = collect_maintenance_alerts(self._repo, cluster_id, self._plant_db)

        detail_key: Literal["notes", "needs_water"] = "notes"
        detail: str | list[str]
        if not irrigator:
            monitor = self.monitor_cluster(cluster_id)
            action, detail_key, detail = "monitored", "needs_water", monitor.get("needs_water", [])
        elif not self._repo.get_effective_config(cluster_id)["auto_run"]["value"]:
            action, detail = "skipped", "auto_run disabled"
        else:
            result = self.run_irrigation_pipeline(cluster_id)
            action, detail = result.get("action", "error"), result.get("reason", "")
        sync_cluster_alerts(self._repo, cluster_id, self._plant_db, notifier=self._notifier)
        return _check_result(
            cluster_id,
            cluster.name,
            action,
            detail_key=detail_key,
            detail=detail,
            alerts=alerts,
            maintenance=maintenance,
        )

    def check_all_clusters(self) -> list[CheckResult]:
        """Check every cluster, isolating each one in its own transaction.

        Each cluster's work is committed as soon as it finishes, so a crash in
        one cluster can never roll back the ``start`` events of pumps that
        already ran for earlier clusters (the cooldown would not see them and
        they could water again on the next tick). A failing cluster is rolled
        back on its own, reported as ``action="error"`` and raises a
        ``check_failed`` alert, which the next successful check resolves.
        """
        clusters = [(c.id, c.name) for c in self._repo.list_clusters()]
        results: list[CheckResult] = []
        for cluster_id, cluster_name in clusters:
            session = self._repo.session
            try:
                result = self.check_cluster(cluster_id)
                self._resolve_stale_check_alert(cluster_id)
                session.commit()
            except Exception as e:
                result = self._record_check_failure(session, cluster_id, cluster_name, e)
            results.append(result)
        return results

    def _resolve_stale_check_alert(self, cluster_id: int) -> None:
        """A cluster that checked cleanly resolves its open ``check_failed`` alert."""
        stale = self._repo.get_active_alert(CHECK_FAILED_ALERT_CODE, cluster_id=cluster_id)
        if stale is not None:
            self._repo.resolve_alert(stale.id)

    def _record_check_failure(
        self, session: "Session", cluster_id: int, cluster_name: str, exc: Exception
    ) -> CheckResult:
        """Roll back the crashed cluster alone, log it, raise ``check_failed`` and commit.

        Called from inside the ``except`` block, so ``logger.exception`` still sees the exception.
        """
        session.rollback()
        logger.exception("Check failed for cluster %s", cluster_id)
        self._repo.upsert_alert(
            f"{CHECK_FAILED_ALERT_CODE}:cluster:{cluster_id}",
            "irrigation",
            CHECK_FAILED_ALERT_CODE,
            f"Check failed: {cluster_name}",
            f"The scheduled check crashed and was skipped for this cluster: {exc!r}",
            severity="error",
            entity_type="cluster",
            entity_id=cluster_id,
            cluster_id=cluster_id,
        )
        session.commit()
        return {
            "cluster_id": cluster_id,
            "cluster_name": cluster_name,
            "action": "error",
            "notes": f"check failed: {exc!r}",
        }
