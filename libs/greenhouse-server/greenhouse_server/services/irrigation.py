"""Irrigation and monitoring orchestration."""

import logging
import time as _time
from datetime import UTC, datetime

from greenhouse_core.devices import DeviceRegistry, UnknownDeviceModel
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.logic.decision import Action, Severity
from greenhouse_core.models import ENTITY_CLUSTER
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import raise_alert, sync_cluster_alerts
from greenhouse_server.services.health_monitor import HEALTH_ALARM_TO_TRIGGER, DeviceHealthMonitor
from greenhouse_server.services.maintenance import collect_learning_alerts, collect_maintenance_alerts
from greenhouse_server.services.notify import NtfyClient, maybe_notify
from greenhouse_server.services.sync import SyncService
from greenhouse_server.services.weather import WeatherClient

logger = logging.getLogger(__name__)

_LEAK_CHECK_DELAY_SECONDS = 1800  # 30 minutes


def schedule_pump_watcher(irrigator_id: int, duration_minutes: int, started_at: int) -> bool:
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

    Returns:
        True if a job was scheduled, False if the scheduler is unavailable,
        the watcher is disabled, the irrigator has no device registry, or
        the duration is non-positive.
    """
    if duration_minutes <= 0:
        return False
    try:
        from greenhouse_server.config import Settings
        from greenhouse_server.scheduler import _app, scheduler

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
                )
                watcher.watch(irrigator, duration_seconds, started_at=started_at)
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

        run_date = datetime.fromtimestamp(started_at + _LEAK_CHECK_DELAY_SECONDS, tz=UTC)
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

        sensor_data = None if no_sync else self._sync.sync_and_read_sensors(cluster_id)
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

        soil_note = (
            f", soil={sensor_data['soil_moisture']:.0f}%"
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
            self._sync.sync_and_read_sensors(cluster_id)

        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        plants_by_id = {p.id: p for p in self._repo.get_plants_in_cluster(cluster_id)}

        sensor_statuses = []
        needs_water = []

        for sensor in sensors:
            readings = self._repo.get_recent_readings(sensor.id, hours=2)
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
        """Check all clusters."""
        clusters = self._repo.list_clusters()
        return [self.check_cluster(c.id) for c in clusters]
