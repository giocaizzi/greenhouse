"""Irrigation and monitoring orchestration."""

import logging
import time as _time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Required, TypedDict, cast

from greenhouse_core.constants import (
    FALLBACK_TEMPERATURE_C,
    MONITOR_LOOKBACK_HOURS,
    MONITOR_VERY_DRY_MARGIN,
    MONITOR_WET_MARGIN,
    SECONDS_PER_HOUR,
    SNAPSHOT_LOOKBACK_HOURS,
)
from greenhouse_core.devices import DeviceRegistry, UnknownDeviceModel
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.logic.cleaning import clean_readings_desc
from greenhouse_core.logic.decision import Action, Reason
from greenhouse_core.logic.plant_needs import moisture_target_range
from greenhouse_core.logic.timing import environment_for
from greenhouse_core.models import (
    ENTITY_CLUSTER,
    EVENT_ACTION_ATTEMPTED,
    EVENT_ACTION_START,
    SOURCE_IRRIGATION,
    TRIGGERED_BY_AUTO,
    TRIGGERED_BY_MANUAL,
    TriggeredBy,
)
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.actuation_gates import first_gate_block
from greenhouse_server.services.alerts import raise_alert, sync_cluster_alerts
from greenhouse_server.services.health_monitor import DeviceHealthMonitor
from greenhouse_server.services.irrigation_jobs import _schedule_leak_check, record_leak_check_armed
from greenhouse_server.services.maintenance import AlertFinding, collect_learning_alerts, collect_maintenance_alerts
from greenhouse_server.services.notify import NtfyClient, maybe_notify
from greenhouse_server.services.pump_watcher_jobs import schedule_pump_watcher
from greenhouse_server.services.sync import ClusterSnapshot, SyncService
from greenhouse_server.services.weather import WeatherClient

if TYPE_CHECKING:
    from collections.abc import Sequence

    from greenhouse_core.devices import AbstractIrrigatorAdapter
    from greenhouse_core.logic.cleaning import CleanedReading
    from greenhouse_core.logic.decision import IrrigationDecision
    from greenhouse_core.models import EventAction, Irrigator

logger = logging.getLogger(__name__)

CHECK_FAILED_ALERT_CODE = "check_failed"


def stop_unrecorded_start(
    repo: IrrigationRepository, adapter: "AbstractIrrigatorAdapter", irrigator: "Irrigator", irrigator_id: int
) -> None:
    """Compensating stop: the pump started but its ``start`` could not be recorded.

    Called from inside the ``except`` of the failed write or commit, so the ERROR record
    carries that error. Rolls the session back first (it may need it, and the reload of the
    irrigator for the stop needs a usable session), then stops the pump so no run continues
    that the cooldown, caps and history cannot see. Never raises: the caller re-raises the
    write error to surface it.
    """
    try:
        repo.rollback()
    except Exception:
        logger.warning("Rollback before the compensating stop failed", exc_info=True)
    try:
        stop_ok, stop_msg = adapter.stop(irrigator)
    except Exception as exc:  # noqa: BLE001 — reported in the ERROR line below
        stop_ok, stop_msg = False, f"adapter.stop raised: {exc}"
    logger.exception(
        "Could not record the start of irrigator %d; compensating stop: stop_ok=%s (%s)",
        irrigator_id,
        stop_ok,
        stop_msg,
    )


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


class MonitorSensorRow(TypedDict):
    """One ``MonitorResult.sensors`` row: the sensor's latest cleaned soil value against its target band."""

    sensor_id: int
    sensor_name: str
    plant_species: str | None
    soil_moisture: float | None
    status: str
    target_min: float
    target_max: float


class MonitorResult(TypedDict):
    """``monitor_cluster`` result: a plain dict at runtime (``MonitorResponse``)."""

    cluster_name: str
    sensors: list[MonitorSensorRow]
    needs_water: list[str]


class CheckResult(TypedDict, total=False):
    """One ``check_cluster`` entry: a plain dict at runtime; ``notes`` or ``needs_water`` per branch."""

    cluster_id: Required[int]
    cluster_name: Required[str]
    action: Required[str]
    notes: str
    needs_water: list[str]
    alerts: list[AlertFinding]
    maintenance: list[AlertFinding]


def check_has_alerts(results: "Sequence[CheckResult]") -> bool:
    """Whether a check needs attention: any cluster has alerts, maintenance items or thirsty plants.

    The one rule behind ``has_alerts`` in the JSON API (``POST /check``) and the web
    check result badge, so both surfaces flag the same checks.
    """
    return any(r.get("alerts") or r.get("maintenance") or r.get("needs_water") for r in results)


def _error_result(reason: str) -> PipelineResult:
    """The pipeline's early-exit shape (an unknown cluster, or no data for a decision)."""
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
    sensor_data: ClusterSnapshot | None
    # ``manual`` for a forced run (``force=true``): the user pushed it, so the event, the push
    # and the decision log all say so. ``auto`` otherwise.
    triggered_by: TriggeredBy = TRIGGERED_BY_AUTO


def _soil_note(sensor_data: ClusterSnapshot | None) -> str:
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


def _monitor_readings(repo: IrrigationRepository, sensor_id: int) -> "list[CleanedReading]":
    """The cleaned readings of the monitor's recent window, newest first.

    Cleaned view: monitor classifies each sensor as dry/ok/wet, and a sensor-only
    cluster has no engine to sanity-check that call. Cleaning runs over the decision
    snapshot's window (``SNAPSHOT_LOOKBACK_HOURS``) so the Hampel filter has enough
    context to judge the newest sample — the monitor's own 2 h slice is too short
    at a 30-min cadence and let a single spike read as very dry. Only then is the
    series cut to ``MONITOR_LOOKBACK_HOURS``.
    """
    cutoff = int(_time.time()) - MONITOR_LOOKBACK_HOURS * SECONDS_PER_HOUR
    history = clean_readings_desc(repo.get_recent_readings(sensor_id, hours=SNAPSHOT_LOOKBACK_HOURS))
    return [r for r in history if r.timestamp >= cutoff]


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
    alerts: list[AlertFinding],
    maintenance: list[AlertFinding],
) -> CheckResult:
    """One ``check_cluster`` entry, keys in the response order; ``detail_key`` names the branch's detail."""
    # A TypedDict literal cannot carry a computed key; the runtime object is the same plain dict.
    return cast(
        "CheckResult",
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
    ) -> None:
        """Wire the pipeline's collaborators; a missing health monitor or notifier falls open (no gate, no push)."""
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
        temp_override: float | None,
        *,
        is_indoor: bool,
        no_sync: bool,
    ) -> tuple[float, str, ClusterSnapshot | None]:
        """Resolve temperature from override, sensor, or weather. Returns (temp, source, sensor_data)."""
        if temp_override is not None:
            return temp_override, "override", None

        sensor_data = None if no_sync else self._sync.ensure_fresh_and_read(cluster_id)
        picked = self._indoor_temperature(sensor_data) if is_indoor else self._outdoor_temperature(sensor_data)
        if picked is not None:
            temp, source = picked
            return temp, source, sensor_data
        return FALLBACK_TEMPERATURE_C, "fallback (20C)", sensor_data

    def _indoor_temperature(self, sensor_data: ClusterSnapshot | None) -> tuple[float, str] | None:
        """Indoor: the cluster's own sensor first, then the weather feels-like; None if neither."""
        if sensor_data and (temperature := sensor_data.get("temperature")) is not None:
            return temperature, "sensor"
        weather = self._weather.get_current()
        if weather and (feels_like := weather.get("feels_like")) is not None:
            return feels_like, "open-meteo (fallback)"
        return None

    def _outdoor_temperature(self, sensor_data: ClusterSnapshot | None) -> tuple[float, str] | None:
        """Outdoor clusters: the weather feels-like first, then the sensor; None if neither."""
        weather = self._weather.get_current()
        if weather and (feels_like := weather.get("feels_like")) is not None:
            return feels_like, "open-meteo"
        if sensor_data and (temperature := sensor_data.get("temperature")) is not None:
            return temperature, "sensor (weather unavailable)"
        return None

    def _decide(self, cluster_id: int, temp: float, *, force: bool) -> "IrrigationDecision | None":
        """Run (and persist) the engine; ``force`` records a manual trigger and bypasses quiet hours."""
        logic = IrrigationLogic(self._repo, self._plant_db, weather_client=self._weather)
        return logic.decide_for_cluster(
            cluster_id,
            current_temp=temp,
            persist=True,
            triggered_by=TRIGGERED_BY_MANUAL if force else TRIGGERED_BY_AUTO,
            bypass_quiet_hours=force,
        )

    def _log_decision_skip(self, cluster_id: int, decision: "IrrigationDecision") -> None:
        """Record an automatic skip in the activity log (no payload, unlike the health-gate skip)."""
        self._repo.add_activity_event(
            source=SOURCE_IRRIGATION,
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
        """Start the pump, durably record the run, then apply the started / failed follow-ups to ``result``."""
        duration = act.decision.duration_minutes
        irrigator_id = act.irrigator.id  # read before any write can fail
        success, output = act.adapter.start(act.irrigator, duration)
        # One timestamp for the event row, the leak check and the watcher, so
        # the restart re-arm (which reads the row) can match the leak check.
        started_at = int(_time.time())
        if not success:
            self._add_event(act, EVENT_ACTION_ATTEMPTED, started_at)
            self._on_start_failed(act, output)
            return _with_error(result, f"irrigator failed: {output}")
        try:
            self._add_event(act, EVENT_ACTION_START, started_at)
            self._record_started(act, started_at)
        except Exception:
            # The pump runs but nothing records it: stop it rather than leave an unrecorded run.
            stop_unrecorded_start(self._repo, act.adapter, act.irrigator, irrigator_id)
            raise
        self._arm_follow_ups(act, started_at)
        result["action"] = "irrigated"
        self._notify_auto_irrigation(act)
        return result

    def _add_event(self, act: _Actuation, action: "EventAction", started_at: int) -> None:
        """The ``start`` / ``attempted`` irrigation event of this run."""
        self._repo.add_irrigation_event(
            irrigator_id=act.irrigator.id,
            action=action,
            duration_minutes=act.decision.duration_minutes,
            triggered_by=act.triggered_by,
            timestamp=started_at,
            notes=_event_notes(act, _soil_note(act.sensor_data)),
        )

    def _record_started(self, act: _Actuation, started_at: int) -> None:
        """Log the run, flag the decision as actuated, and commit before anything else can fail.

        The pump is already running, so its ``start`` event must be durable now: a later failure
        (the follow-up scheduling, the push, or the rest of a ``check_all`` cluster) rolls back
        only what comes after, and the 6 h cooldown still sees this run (S3).
        """
        duration = act.decision.duration_minutes
        self._repo.add_activity_event(
            source=SOURCE_IRRIGATION,
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
        if act.triggered_by != TRIGGERED_BY_AUTO:
            record_leak_check_armed(self._repo, act.irrigator, started_at, triggered_by=act.triggered_by)
        self._repo.commit()

    @staticmethod
    def _arm_follow_ups(act: _Actuation, started_at: int) -> None:
        """Arm the post-irrigation leak check and the dry-run watcher for a started run.

        A forced run gets the leak check too: it runs the engine's computed duration unattended,
        exactly like an automatic one, so a stuck valve after it must raise the hold.
        """
        _schedule_leak_check(act.cluster_id, started_at)
        schedule_pump_watcher(
            act.irrigator.id,
            act.decision.duration_minutes,
            started_at,
            triggered_by=act.triggered_by,
            forced=act.triggered_by != TRIGGERED_BY_AUTO,
        )

    def _notify_auto_irrigation(self, act: _Actuation) -> None:
        """Push the irrigation notice under the run's own trigger (``auto``, or ``manual`` when forced).

        ``get_preferences`` still runs as an argument: it may insert.
        """
        duration = act.decision.duration_minutes
        maybe_notify(
            self._notifier,
            self._repo.get_preferences(),
            "auto" if act.triggered_by == TRIGGERED_BY_AUTO else "manual",
            lambda n: n.notify_irrigation(
                triggered_by=act.triggered_by,
                irrigator_name=act.irrigator.name,
                duration_minutes=duration,
                detail=f"confidence={act.decision.confidence:.0%}",
            ),
        )

    def _on_start_failed(self, act: _Actuation, output: str) -> None:
        """Log the failed start and raise the actuation-failed alert."""
        self._repo.add_activity_event(
            source=SOURCE_IRRIGATION,
            entity_type=ENTITY_CLUSTER,
            entity_id=act.cluster_id,
            code="actuation_failed",
            message=f"irrigator failed: {output}",
            severity="warning",
        )
        raise_alert(
            self._repo,
            source=SOURCE_IRRIGATION,
            code="actuation_failed",
            title="Irrigation Actuation Failed",
            message=f"Irrigator '{act.irrigator.name}' failed to start: {output}",
            severity="warning",
            cluster_id=act.cluster_id,
            notifier=self._notifier,
        )

    def _actuation_gates_skip(
        self, cluster_id: int, irrigator: "Irrigator", decision: "IrrigationDecision", result: PipelineResult
    ) -> bool:
        """Run the server's actuation gates (:mod:`.actuation_gates`); the first block becomes a logged skip.

        Order: device health, the per-day caps, then the global dry run. On a device-health
        block ``result`` also lists the blocking alarms. Returns True when the run became a skip.
        """
        block = first_gate_block(self._repo, self._health_monitor, cluster_id, irrigator, decision)
        if block is None:
            return False
        self._skip_run(cluster_id, decision, result, block.lead, payload=block.payload)
        if block.blocking_alarms is not None:
            result["blocking_alarms"] = block.blocking_alarms
        return True

    def _skip_run(
        self,
        cluster_id: int,
        decision: "IrrigationDecision",
        result: PipelineResult,
        lead: Reason,
        *,
        payload: dict[str, Any],
    ) -> None:
        """Turn the engine's IRRIGATE into a server-side skip that every record agrees on.

        ``lead`` goes first in the reason trail, so it becomes the decision's ``primary_code``;
        the engine's ``decision_logs`` row is rewritten to the skip (B-6: it used to keep
        ``irrigate``), a ``decision_skip`` activity row carries ``payload``, and ``result`` is
        rewritten in place.
        """
        decision.reasons.insert(0, lead)
        decision.action = Action.SKIP
        if decision.decision_log_id is not None:
            self._repo.rewrite_decision_log(
                decision.decision_log_id,
                action=decision.action.value,
                primary_code=lead.code.value,
                reason_text=decision.reason_text,
                payload=decision.model_dump(mode="json"),
            )
        self._repo.add_activity_event(
            source=SOURCE_IRRIGATION,
            entity_type=ENTITY_CLUSTER,
            entity_id=cluster_id,
            code="decision_skip",
            message=decision.reason_text,
            severity="warning",
            payload=payload,
        )
        result["action"] = "skip"
        result["reason"] = decision.reason_text
        result["reasons"] = [r.model_dump() for r in decision.reasons]

    def run_irrigation_pipeline(
        self,
        cluster_id: int,
        temp_override: float | None = None,
        *,
        dry_run: bool = False,
        no_sync: bool = False,
        force: bool = False,
    ) -> PipelineResult:
        """Full pipeline: sync -> weather -> decide -> execute. Returns result dict.

        ``force=True`` bypasses the quiet-hours gate inside the decision
        engine; a ``MANUAL_OVERRIDE_QUIET_HOURS`` warning Reason is still
        attached to the resulting decision so the audit log records that
        the user pushed past the deny window. A forced run is the user's: its
        decision log, irrigation event and push say ``manual``; it still gets
        the post-watering leak check and the dry-run watcher.

        An IRRIGATE decision then passes the server's actuation gates, in order: the
        device-health gate, the per-day caps, then the global dry run (``dry_run_global``).
        Each turns the run into a logged skip; nothing bypasses them, ``force`` included.
        """
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return _error_result("cluster not found")

        # The engine's outdoor rule: only "outdoor" prefers the weather temperature.
        is_indoor = environment_for(cluster.environment) == "indoor"
        temp, source, sensor_data = self._resolve_temperature(
            cluster_id, temp_override, is_indoor=is_indoor, no_sync=no_sync
        )

        decision = self._decide(cluster_id, temp, force=force)
        if not decision:
            return _error_result("no data for decision")

        result = _decision_result(decision, temp, source)

        if dry_run:
            return result
        if decision.action is Action.SKIP:
            self._log_decision_skip(cluster_id, decision)
            return result

        target = self._actuation_target(cluster_id)
        if isinstance(target, str):
            return _with_error(result, target)
        irrigator, adapter = target

        if not self._actuation_gates_skip(cluster_id, irrigator, decision, result):
            trigger = TRIGGERED_BY_MANUAL if force else TRIGGERED_BY_AUTO
            self._actuate(
                _Actuation(cluster_id, irrigator, adapter, decision, temp, source, sensor_data, trigger), result
            )
        return result

    def monitor_cluster(self, cluster_id: int) -> MonitorResult:
        """Monitor a sensor-only cluster: refresh stale sensors (the caller commits), return per-sensor soil status."""
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return {"cluster_name": "unknown", "sensors": [], "needs_water": []}

        self._sync.ensure_fresh_and_read(cluster_id)

        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        plants_by_id = {p.id: p for p in self._repo.get_plants_in_cluster(cluster_id)}

        sensor_statuses: list[MonitorSensorRow] = []
        needs_water: list[str] = []

        for sensor in sensors:
            latest_soil = _latest_soil(_monitor_readings(self._repo, sensor.id))

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
        ``check_failed`` alert, which the next successful check resolves. The
        rollback cannot erase the failing cluster's own pump run either: the
        pipeline commits a successful start (event, ``irrigated`` activity,
        actuated decision log) the moment the device accepts it.
        """
        clusters = [(c.id, c.name) for c in self._repo.list_clusters()]
        results: list[CheckResult] = []
        for cluster_id, cluster_name in clusters:
            try:
                result = self.check_cluster(cluster_id)
                self._resolve_stale_check_alert(cluster_id)
                self._repo.commit()
            except Exception as e:  # noqa: BLE001
                result = self._record_check_failure(cluster_id, cluster_name, e)
            results.append(result)
        return results

    def _resolve_stale_check_alert(self, cluster_id: int) -> None:
        """A cluster that checked cleanly resolves its open ``check_failed`` alert."""
        stale = self._repo.get_active_alert(CHECK_FAILED_ALERT_CODE, cluster_id=cluster_id)
        if stale is not None:
            self._repo.resolve_alert(stale.id)

    def _record_check_failure(self, cluster_id: int, cluster_name: str, exc: Exception) -> CheckResult:
        """Roll back the crashed cluster alone, log it, raise ``check_failed`` and commit.

        Called from inside the ``except`` block, so ``logger.exception`` still sees the exception.
        """
        self._repo.rollback()
        logger.exception("Check failed for cluster %s", cluster_id)
        self._repo.upsert_alert(
            f"{CHECK_FAILED_ALERT_CODE}:cluster:{cluster_id}",
            SOURCE_IRRIGATION,
            CHECK_FAILED_ALERT_CODE,
            f"Check failed: {cluster_name}",
            f"The scheduled check crashed and was skipped for this cluster: {exc!r}",
            severity="error",
            entity_type=ENTITY_CLUSTER,
            entity_id=cluster_id,
            cluster_id=cluster_id,
        )
        self._repo.commit()
        return {
            "cluster_id": cluster_id,
            "cluster_name": cluster_name,
            "action": "error",
            "notes": f"check failed: {exc!r}",
        }
