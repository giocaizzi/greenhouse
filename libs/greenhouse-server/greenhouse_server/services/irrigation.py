"""Irrigation and monitoring orchestration."""

import logging
import time as _time
from datetime import UTC, datetime

from greenhouse_core.constants import LEAK_CHECK_DELAY_SECONDS
from greenhouse_core.devices import DeviceRegistry, UnknownDeviceModel
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.logic.cleaning import clean_readings_desc
from greenhouse_core.logic.decision import Action, Severity
from greenhouse_core.models import ENTITY_CLUSTER, ENTITY_IRRIGATOR
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import raise_alert, sync_cluster_alerts
from greenhouse_server.services.health_monitor import HEALTH_ALARM_TO_TRIGGER, DeviceHealthMonitor
from greenhouse_server.services.maintenance import collect_learning_alerts, collect_maintenance_alerts
from greenhouse_server.services.notify import NtfyClient, maybe_notify
from greenhouse_server.services.sync import SyncService
from greenhouse_server.services.weather import WeatherClient

logger = logging.getLogger(__name__)

CHECK_FAILED_ALERT_CODE = "check_failed"


WATCHER_SHUTDOWN_ACTIVITY_CODE = "pump_watcher_shutdown"


def handle_watcher_interrupted(
    repo: IrrigationRepository,
    registry: DeviceRegistry,
    irrigator,
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
            message = f"Server shutdown stopped the auto irrigation on '{irrigator.name}' (watcher interrupted)"
        else:
            logger.error(
                "Server shutting down mid-irrigation: FAILED to stop auto cycle on irrigator %d (%s) — "
                "it continues unprotected until the device timer ends it",
                irrigator.id,
                stop_msg,
            )
            message = (
                f"Server shutdown could not stop the auto irrigation on '{irrigator.name}' ({stop_msg}); "
                "it continues without dry-run protection"
            )
    else:
        logger.warning(
            "Server shutting down mid-irrigation: %s cycle on irrigator %d left running "
            "unprotected (no dry-run watcher) until the device timer ends it",
            triggered_by,
            irrigator.id,
        )
        message = (
            f"Server shutdown: {triggered_by} irrigation on '{irrigator.name}' continues "
            "without dry-run protection until the device timer ends it"
        )
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
        from greenhouse_server.config import Settings
        from greenhouse_server.scheduler import _app, scheduler, shutdown_requested, wait_for_shutdown

        if not scheduler.running or _app is None:
            return False

        settings: Settings = getattr(_app.state, "settings", None)
        if settings is not None and not settings.pump_watcher_enabled:
            return False

        registry: DeviceRegistry | None = getattr(_app.state, "device_registry", None)
        if registry is None:
            return False

        run_date = datetime.fromtimestamp(started_at, tz=UTC)
        job_id = f"pump-watcher-{irrigator_id}-{started_at}"
        duration_seconds = int(duration_minutes * 60)

        def _run() -> None:
            from greenhouse_core.repository import IrrigationRepository
            from greenhouse_server.services.pump_watcher import PumpWatcherService

            session = _app.state.session_factory()
            try:
                repo = IrrigationRepository(session)
                irrigator = repo.get_irrigator(irrigator_id)
                if irrigator is None:
                    return
                watcher_settings = getattr(_app.state, "settings", None)
                if watcher_settings is None:
                    poll = 2.0
                    warmup = 5.0
                    max_failures = 5
                else:
                    poll = watcher_settings.pump_watcher_poll_seconds
                    warmup = watcher_settings.pump_watcher_warmup_seconds
                    max_failures = watcher_settings.pump_watcher_max_read_failures
                monitor = getattr(_app.state, "health_monitor", None)
                if monitor is not None:
                    monitor.bind_repo(repo)
                watcher = PumpWatcherService(
                    repo,
                    registry,
                    poll_seconds=poll,
                    warmup_seconds=warmup,
                    max_read_failures=max_failures,
                    monitor=monitor,
                    sleep=wait_for_shutdown,
                    stop_requested=shutdown_requested,
                )
                result = watcher.watch(irrigator, duration_seconds, started_at=started_at)
                if result["outcome"] == "interrupted":
                    handle_watcher_interrupted(
                        repo, registry, irrigator, triggered_by=triggered_by, started_at=started_at
                    )
                    session.commit()
            except Exception:
                session.rollback()
                logger.exception("Pump watcher job failed for irrigator %d", irrigator_id)
            finally:
                session.close()

        scheduler.add_job(
            _run,
            "date",
            run_date=run_date,
            id=job_id,
            name=f"Pump watcher irrigator {irrigator_id}",
            replace_existing=True,
        )
        return True
    except Exception:
        logger.debug("Could not schedule pump watcher for irrigator %d", irrigator_id, exc_info=True)
        return False


def _schedule_leak_check(cluster_id: int, started_at: int) -> None:
    """Schedule a one-shot leak detection check 30 minutes after an irrigation start.

    Skips silently when the scheduler is not running (test environments).
    Tests should call ``LeakDetectionService.check_after_irrigation`` directly.
    """
    try:
        from greenhouse_server.scheduler import _app, scheduler

        if not scheduler.running:
            return

        run_date = datetime.fromtimestamp(started_at + LEAK_CHECK_DELAY_SECONDS, tz=UTC)
        job_id = f"leak-check-{cluster_id}-{started_at}"

        def _run() -> None:
            if _app is None:
                return
            from greenhouse_core.repository import IrrigationRepository
            from greenhouse_server.services.leak import LeakDetectionService

            session = _app.state.session_factory()
            try:
                repo = IrrigationRepository(session)
                LeakDetectionService(
                    repo, _app.state.plant_db, notifier=getattr(_app.state, "ntfy_notifier", None)
                ).check_after_irrigation(cluster_id, started_at)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Leak check job failed for cluster %d", cluster_id)
            finally:
                session.close()

        scheduler.add_job(
            _run,
            "date",
            run_date=run_date,
            id=job_id,
            name=f"Leak check cluster {cluster_id}",
            replace_existing=True,
        )
    except Exception:
        # Scheduling must never block irrigation
        logger.debug("Could not schedule leak check for cluster %d", cluster_id, exc_info=True)


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
    ) -> tuple[float, str, dict | None]:
        """Resolve temperature from override, sensor, or weather. Returns (temp, source, sensor_data)."""
        if temp_override is not None:
            return temp_override, "override", None

        sensor_data = None if no_sync else self._sync.ensure_fresh_and_read(cluster_id)
        weather = None

        if is_indoor:
            if sensor_data and sensor_data.get("temperature") is not None:
                return sensor_data["temperature"], "sensor", sensor_data
            weather = self._weather.get_current()
            if weather and weather.get("feels_like") is not None:
                return weather["feels_like"], "open-meteo (fallback)", sensor_data
        else:
            weather = self._weather.get_current()
            if weather and weather.get("feels_like") is not None:
                return weather["feels_like"], "open-meteo", sensor_data
            if sensor_data and sensor_data.get("temperature") is not None:
                return sensor_data["temperature"], "sensor (weather unavailable)", sensor_data

        return 20.0, "fallback (20C)", sensor_data

    def run_irrigation_pipeline(
        self,
        cluster_id: int,
        temp_override: float | None = None,
        dry_run: bool = False,
        no_sync: bool = False,
        force: bool = False,
    ) -> dict:
        """Full pipeline: sync -> weather -> decide -> execute. Returns result dict.

        ``force=True`` bypasses the quiet-hours gate inside the decision
        engine; a ``MANUAL_OVERRIDE_QUIET_HOURS`` warning Reason is still
        attached to the resulting decision so the audit log records that
        the user pushed past the deny window.
        """
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return {"action": "error", "reason": "cluster not found", "confidence": 0}

        is_indoor = cluster.environment == "indoor"
        temp, source, sensor_data = self._resolve_temperature(cluster_id, is_indoor, temp_override, no_sync)

        logic = IrrigationLogic(self._repo, self._plant_db, weather_client=self._weather)
        decision = logic.decide_for_cluster(
            cluster_id,
            current_temp=temp,
            persist=True,
            triggered_by="manual" if force else "auto",
            bypass_quiet_hours=force,
        )
        if not decision:
            return {"action": "error", "reason": "no data for decision", "confidence": 0}

        result = {
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

        if dry_run or decision.action.value == "skip":
            if not dry_run:
                self._repo.add_activity_event(
                    source="irrigation",
                    entity_type=ENTITY_CLUSTER,
                    entity_id=cluster_id,
                    code="decision_skip",
                    message=decision.reason_text,
                    severity="info",
                )
            return result

        # Execute
        irrigator = self._repo.get_irrigator_for_cluster(cluster_id)
        if not irrigator:
            result["action"] = "error"
            result["reason"] = "no irrigators found"
            return result
        if self._registry is None:
            result["action"] = "error"
            result["reason"] = "no device registry"
            return result

        try:
            adapter = self._registry.get_irrigator(irrigator)
        except UnknownDeviceModel as exc:
            result["action"] = "error"
            result["reason"] = f"no adapter for irrigator: {exc}"
            return result

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

        duration = decision.duration_minutes
        success, output = adapter.start(irrigator, duration)

        # The snapshot's soil value is the cluster's driest sensor (invariant #2),
        # so label it as such — an unqualified "soil=" reads as "this plant's".
        soil_note = (
            f", soil={sensor_data['soil_moisture']:.0f}% (driest)"
            if sensor_data and sensor_data.get("soil_moisture") is not None
            else ""
        )
        self._repo.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="start" if success else "attempted",
            duration_minutes=duration,
            triggered_by="auto",
            notes=(
                f"temp={temp:.1f}C ({source}){soil_note}, "
                f"confidence={decision.confidence:.0%}, reason={decision.reason_text}"
            ),
        )

        if success:
            self._repo.add_activity_event(
                source="irrigation",
                entity_type=ENTITY_CLUSTER,
                entity_id=cluster_id,
                code="irrigated",
                message=f"irrigated for {duration}min (confidence={decision.confidence:.0%})",
                severity="info",
                payload={
                    "irrigator_id": irrigator.id,
                    "duration_minutes": duration,
                    "confidence": decision.confidence,
                },
            )
            if decision.decision_log_id is not None:
                self._repo.set_decision_actuated(decision.decision_log_id)
            started_at = int(_time.time())
            _schedule_leak_check(cluster_id, started_at)
            schedule_pump_watcher(irrigator.id, duration, started_at)
            result["action"] = "irrigated"
            maybe_notify(
                self._notifier,
                self._repo.get_preferences(),
                "auto",
                lambda: self._notifier.notify_irrigation(
                    triggered_by="auto",
                    irrigator_name=irrigator.name,
                    duration_minutes=duration,
                    detail=f"confidence={decision.confidence:.0%}",
                ),
            )
        else:
            self._repo.add_activity_event(
                source="irrigation",
                entity_type=ENTITY_CLUSTER,
                entity_id=cluster_id,
                code="actuation_failed",
                message=f"irrigator failed: {output}",
                severity="warning",
            )
            raise_alert(
                self._repo,
                source="irrigation",
                code="actuation_failed",
                title="Irrigation Actuation Failed",
                message=f"Irrigator '{irrigator.name}' failed to start: {output}",
                severity="warning",
                cluster_id=cluster_id,
                notifier=self._notifier,
            )
            result["action"] = "error"
            result["reason"] = f"irrigator failed: {output}"

        return result

    def monitor_cluster(self, cluster_id: int, no_sync: bool = False) -> dict:
        """Monitor sensor-only cluster. Returns per-sensor soil status."""
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return {"cluster_name": "unknown", "sensors": [], "needs_water": []}

        if not no_sync:
            self._sync.ensure_fresh_and_read(cluster_id)

        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        plants_by_id = {p.id: p for p in self._repo.get_plants_in_cluster(cluster_id)}

        sensor_statuses = []
        needs_water = []

        for sensor in sensors:
            # Cleaned view: monitor classifies each sensor as dry/ok/wet, and a
            # sensor-only cluster has no engine to sanity-check that call.
            readings = clean_readings_desc(self._repo.get_recent_readings(sensor.id, hours=2))
            latest_soil = (
                next((r.soil_moisture for r in readings if r.soil_moisture is not None), None) if readings else None
            )

            plant = plants_by_id.get(sensor.plant_id) if sensor.plant_id else None
            care = self._plant_db.get_care_data(species=plant.species if plant else None)
            target_raw = care.get("soil_moisture_target", "45-65")
            try:
                t_min, t_max = (float(x) for x in target_raw.split("-"))
            except Exception:
                t_min, t_max = 45.0, 65.0

            if latest_soil is None:
                status = "no_data"
            elif latest_soil < t_min - 15:
                status = "very_dry"
            elif latest_soil < t_min:
                status = "dry"
            elif latest_soil > t_max + 10:
                status = "wet"
            else:
                status = "ok"

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

        return {
            "cluster_name": cluster.name,
            "sensors": sensor_statuses,
            "needs_water": needs_water,
        }

    def check_cluster(self, cluster_id: int) -> dict:
        """Check a single cluster: irrigate if has irrigators, monitor otherwise."""
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return {"cluster_id": cluster_id, "cluster_name": "unknown", "action": "error", "notes": "not found"}

        irrigator = self._repo.get_irrigator_for_cluster(cluster_id)
        alerts = collect_learning_alerts(self._repo, cluster_id, self._plant_db)
        maintenance = collect_maintenance_alerts(self._repo, cluster_id, self._plant_db)

        if irrigator:
            effective = self._repo.get_effective_config(cluster_id)
            if not effective["auto_run"]["value"]:
                sync_cluster_alerts(self._repo, cluster_id, self._plant_db, notifier=self._notifier)
                return {
                    "cluster_id": cluster_id,
                    "cluster_name": cluster.name,
                    "action": "skipped",
                    "notes": "auto_run disabled",
                    "alerts": alerts,
                    "maintenance": maintenance,
                }

            result = self.run_irrigation_pipeline(cluster_id)
            sync_cluster_alerts(self._repo, cluster_id, self._plant_db, notifier=self._notifier)
            return {
                "cluster_id": cluster_id,
                "cluster_name": cluster.name,
                "action": result.get("action", "error"),
                "notes": result.get("reason", ""),
                "alerts": alerts,
                "maintenance": maintenance,
            }
        else:
            monitor = self.monitor_cluster(cluster_id)
            sync_cluster_alerts(self._repo, cluster_id, self._plant_db, notifier=self._notifier)
            return {
                "cluster_id": cluster_id,
                "cluster_name": cluster.name,
                "action": "monitored",
                "needs_water": monitor.get("needs_water", []),
                "alerts": alerts,
                "maintenance": maintenance,
            }

    def check_all_clusters(self) -> list[dict]:
        """Check every cluster, isolating each one in its own transaction.

        Each cluster's work is committed as soon as it finishes, so a crash in
        one cluster can never roll back the ``start`` events of pumps that
        already ran for earlier clusters (the cooldown would not see them and
        they could water again on the next tick). A failing cluster is rolled
        back on its own, reported as ``action="error"`` and raises a
        ``check_failed`` alert, which the next successful check resolves.
        """
        clusters = [(c.id, c.name) for c in self._repo.list_clusters()]
        results = []
        for cluster_id, cluster_name in clusters:
            session = self._repo.session
            try:
                result = self.check_cluster(cluster_id)
                stale = self._repo.get_active_alert(CHECK_FAILED_ALERT_CODE, cluster_id=cluster_id)
                if stale is not None:
                    self._repo.resolve_alert(stale.id)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.exception("Check failed for cluster %s", cluster_id)
                self._repo.upsert_alert(
                    f"{CHECK_FAILED_ALERT_CODE}:cluster:{cluster_id}",
                    "irrigation",
                    CHECK_FAILED_ALERT_CODE,
                    f"Check failed: {cluster_name}",
                    f"The scheduled check crashed and was skipped for this cluster: {e!r}",
                    severity="error",
                    entity_type="cluster",
                    entity_id=cluster_id,
                    cluster_id=cluster_id,
                )
                session.commit()
                result = {
                    "cluster_id": cluster_id,
                    "cluster_name": cluster_name,
                    "action": "error",
                    "notes": f"check failed: {e!r}",
                }
            results.append(result)
        return results
