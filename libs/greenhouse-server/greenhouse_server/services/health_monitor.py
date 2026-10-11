"""Device health monitor — single source of truth for health alarms.

Polls every registered irrigator + sensor on a slow cadence, diffs the
returned :class:`DeviceHealthState` against the last known snapshot, and
raises / resolves typed alerts on transitions. The PumpWatcherService
remains the fast-path (sub-2s) watchdog during active irrigations; it
records its observations into this monitor so the two state machines stay
coherent and there is a single dedup_key scheme (``health:{entity}:{id}:{alarm}``).

Engine code consults :meth:`is_actuation_blocked` before firing the pump.
A blocked irrigator forces ``Action.SKIP`` with a typed
:class:`~greenhouse_core.logic.decision.Reason` so the audit trail in
``decision_logs`` explains why we held off.

The monitor holds two pieces of state:

* a per-entity cache of the last derived alarm set, used by
  :meth:`is_actuation_blocked` so the engine doesn't re-poll the hardware.
  One cache (and one lock guarding it) is shared by the app-wide instance and
  every per-session view of it;
* the ``repo`` it writes alerts through. Each job or watcher takes its own view
  with :meth:`for_repo`, so a minutes-long pump watch and the other scheduler
  jobs never write into each other's sessions (S6: a shared, re-bound ``repo``
  sent a dry-run alert into another job's transaction, which could roll it
  back while the cache already marked it raised).
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from greenhouse_core.constants import (
    BATTERY_CRITICAL_PCT,
    BATTERY_LOW_PCT,
    DEFAULT_SYNC_INTERVAL_MINUTES,
    OFFLINE_AFTER_MINUTES,
    SENSOR_HEALTH_BACKFILL_HOURS,
    SENSOR_HEALTH_BACKFILL_WINDOW,
    SENSOR_OFFLINE_GRACE_MINUTES,
    SIGNAL_LOSS_THRESHOLD,
)
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.logic.decision import DEVICE_BLOCKING_CODES
from greenhouse_core.models import ENTITY_IRRIGATOR, ENTITY_SENSOR, SOURCE_HEALTH, EntityType, Irrigator, Sensor
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import is_alert_open, notify_if_new_alert
from greenhouse_server.services.health_alarms import (
    HEALTH_ALARM_TO_TRIGGER,
    alarm_message,
    alarm_severity,
    alarm_title,
    health_dedup_key,
    infer_cluster_id,
    infer_label,
    is_low_battery_state,
    unresolved_alarms_by_entity,
)
from greenhouse_server.services.notify import NtfyClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Cached:
    """Per-entity cached state used for transition diffing + actuation gating.

    ``unraised`` holds derived alarms whose alert write failed: they still gate actuation (a
    dry pump stays blocked) and their alert is retried on the next observation.
    """

    state: DeviceHealthState
    derived_alarms: frozenset[HealthAlarm]
    unraised: frozenset[HealthAlarm] = frozenset()


class DeviceHealthMonitor:
    """Polls device health, diffs state, raises typed alerts on transitions."""

    def __init__(
        self,
        repo: IrrigationRepository,
        registry: DeviceRegistry,
        *,
        battery_low_pct: int = BATTERY_LOW_PCT,
        battery_critical_pct: int = BATTERY_CRITICAL_PCT,
        offline_after_minutes: int = OFFLINE_AFTER_MINUTES,
        sensor_offline_after_minutes: int = DEFAULT_SYNC_INTERVAL_MINUTES + SENSOR_OFFLINE_GRACE_MINUTES,
        signal_loss_threshold: int = SIGNAL_LOSS_THRESHOLD,
        clock: Callable[[], int] = lambda: int(time.time()),
        notifier: NtfyClient | None = None,
    ) -> None:
        """Build a monitor.

        ``offline_after_minutes`` judges irrigators, whose ``last_seen_ts`` comes from a live
        read; ``sensor_offline_after_minutes`` judges sensors, whose ``last_seen_ts`` is the
        latest persisted reading — pass the sync interval plus ``SENSOR_OFFLINE_GRACE_MINUTES``
        (B-3: the 30-min irrigator window flagged every sensor offline between syncs).
        """
        self._repo = repo
        self._registry = registry
        self._notifier = notifier
        self._battery_low_pct = battery_low_pct
        self._battery_critical_pct = battery_critical_pct
        self._offline_after_seconds = offline_after_minutes * 60
        self._sensor_offline_after_seconds = sensor_offline_after_minutes * 60
        self._signal_loss_threshold = signal_loss_threshold
        self._clock = clock
        self._cache: dict[tuple[str, int], _Cached] = {}
        # Guards every read-modify-write of the shared cache: the health job, the check job,
        # pump watchers and request threads all reach it concurrently.
        self._lock = threading.RLock()

    def for_repo(self, repo: IrrigationRepository) -> DeviceHealthMonitor:
        """A view of this monitor that writes through ``repo``, sharing its cache and lock.

        Every scheduler job and pump watcher takes its own view on its own session, so the
        alerts it raises land in — and commit or roll back with — its own transaction, while
        transitions and the actuation gate stay one state machine.
        """
        view = copy.copy(self)
        view._repo = repo  # noqa: SLF001 — sets the field on a copy of this very class
        return view

    # ── Public poll API ───────────────────────────────────────────────────

    def poll_irrigator(self, irrigator: Irrigator) -> DeviceHealthState:
        """Read the irrigator's health surface and reconcile alerts."""
        adapter = self._registry.get_irrigator(irrigator)
        state = adapter.read_health(irrigator)
        self.record(ENTITY_IRRIGATOR, irrigator.id, state, label=irrigator.name)
        return state

    def poll_sensor(self, sensor: Sensor) -> DeviceHealthState:
        """Reconcile a sensor's health from its latest persisted reading.

        The sync job is the sole Cloud writer of sensor readings; the adapter
        derives battery / water-warning / recency from the row we pass in, so
        this poll issues no Cloud call.
        """
        adapter = self._registry.get_sensor(sensor)
        if adapter is None:
            return DeviceHealthState(observed_at=self._clock(), offline=False)
        latest = self._repo.get_latest_reading(sensor.id)
        state = adapter.read_health(sensor, latest)
        self.record(ENTITY_SENSOR, sensor.id, state, label=sensor.name)
        return state

    def poll_all(self) -> None:
        """Poll every irrigator and sensor in the registry. Best-effort."""
        for irrigator in self._repo.list_all_irrigators():
            try:
                self.poll_irrigator(irrigator)
            except Exception:
                logger.exception("Health poll failed for irrigator %d", irrigator.id)
        for sensor in self._repo.list_all_sensors():
            try:
                self.poll_sensor(sensor)
            except Exception:
                logger.exception("Health poll failed for sensor %d", sensor.id)

    # ── Recording (shared with PumpWatcherService) ────────────────────────

    def record(
        self,
        entity_type: EntityType,
        entity_id: int,
        state: DeviceHealthState,
        *,
        label: str | None = None,
        cluster_id: int | None = None,
    ) -> frozenset[HealthAlarm]:
        """Diff vs the cached state and emit/resolve alerts on transitions.

        Returns the full set of derived alarms so the caller can
        short-circuit on a fresh transition.
        """
        offline_after = (
            self._sensor_offline_after_seconds if entity_type == ENTITY_SENSOR else self._offline_after_seconds
        )
        derived = self._derive_alarms(state, offline_after_seconds=offline_after)
        key = (entity_type, entity_id)
        with self._lock:
            previous = self._cache.get(key)
            prev_alarms = previous.derived_alarms if previous else frozenset()
            # New transitions, plus alarms whose earlier alert write failed (retried until it lands).
            to_raise = (derived - prev_alarms) | (derived & (previous.unraised if previous else frozenset()))
            disappeared = prev_alarms - derived

            cluster_id = cluster_id or infer_cluster_id(self._repo, entity_type, entity_id)
            label = label or infer_label(self._repo, entity_type, entity_id) or f"{entity_type}#{entity_id}"

            unraised = frozenset(
                alarm
                for alarm in to_raise
                if not self._raise_health_alert(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    alarm=alarm,
                    state=state,
                    label=label,
                    cluster_id=cluster_id,
                )
            )
            for alarm in disappeared:
                self._resolve_health_alert(entity_type=entity_type, entity_id=entity_id, alarm=alarm)

            self._cache[key] = _Cached(state=state, derived_alarms=derived, unraised=unraised)
        return derived

    def mark_unraised(self, entity_type: EntityType, entity_id: int, alarm: HealthAlarm) -> None:
        """Flag a cached alarm whose alert was lost after the write (its transaction rolled back).

        The alarm keeps gating actuation; its alert is raised again on the next observation.
        No-op when the entity's cache does not hold the alarm.
        """
        key = (entity_type, entity_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and alarm in cached.derived_alarms:
                self._cache[key] = replace(cached, unraised=cached.unraised | {alarm})

    # ── Engine gate ───────────────────────────────────────────────────────

    def is_actuation_blocked(self, irrigator: Irrigator) -> tuple[bool, list[HealthAlarm]]:
        """Should the engine hold off actuating this irrigator?

        Reads the cached derived alarms only — never re-polls the device.
        Returns the list of blocking alarms so the caller can build a
        :class:`Reason` for the decision audit trail.
        """
        with self._lock:
            cached = self._cache.get((ENTITY_IRRIGATOR, irrigator.id))
        if cached is None:
            return False, []
        # An alarm blocks when its trigger code is a device-blocking one; SENSOR_FAULT has no code.
        blocking: list[HealthAlarm] = [
            alarm for alarm in cached.derived_alarms if HEALTH_ALARM_TO_TRIGGER.get(alarm) in DEVICE_BLOCKING_CODES
        ]
        return bool(blocking), blocking

    # ── Restore from the inbox (startup hook) ─────────────────────────────

    def restore_from_open_alerts(self) -> int:
        """Rebuild the cache from the unresolved health alerts; call once at startup, after the backfill.

        The cache is memory-only, so after a restart an open NO_WATER / OFFLINE alert did not
        block actuation until the next poll, and an alarm that cleared while the server was down
        was never auto-resolved (B-9). Seeding the cache with the alarms the inbox still holds
        restores the gate at once; the next poll then diffs against them as usual, resolving
        what cleared and leaving what persists alone (no duplicate raise or push).

        Returns:
            How many entities were restored.
        """
        restored = unresolved_alarms_by_entity(self._repo)
        now = self._clock()
        with self._lock:
            for key, alarms in restored.items():
                if key not in self._cache:  # a live observation already made wins
                    self._cache[key] = _Cached(state=DeviceHealthState(observed_at=now), derived_alarms=alarms)
        return len(restored)

    # ── Backfill from SensorReading history (startup hook) ────────────────

    def backfill_from_history(self, *, window: int = SENSOR_HEALTH_BACKFILL_WINDOW) -> None:
        """Raise low-battery / sensor-fault alerts derived from recent readings.

        Only persistent signals are back-filled — transient ones (offline,
        signal-loss) must come from a live read. Prevents the server
        forgetting a known-bad battery state across a restart.
        """
        for sensor in self._repo.list_all_sensors():
            readings = self._repo.get_recent_readings(sensor.id, hours=SENSOR_HEALTH_BACKFILL_HOURS)
            if not readings:
                continue
            recent = readings[:window]
            if len(recent) < window:
                continue
            cluster_id = sensor.cluster_id

            if all(is_low_battery_state(r.battery_state) for r in recent):
                self._raise_if_not_open(HealthAlarm.LOW_BATTERY, sensor, cluster_id=cluster_id)

            if all(r.water_warning is True for r in recent):
                self._raise_if_not_open(HealthAlarm.SENSOR_FAULT, sensor, cluster_id=cluster_id)

    def _raise_if_not_open(self, alarm: HealthAlarm, sensor: Sensor, *, cluster_id: int | None) -> None:
        """Raise a back-filled sensor alarm unless its alert is already open (no duplicate on restart)."""
        key = health_dedup_key(ENTITY_SENSOR, sensor.id, alarm)
        if not self._repo.get_open_alert_by_key(key):
            self._raise_health_alert(
                entity_type=ENTITY_SENSOR,
                entity_id=sensor.id,
                alarm=alarm,
                state=DeviceHealthState(observed_at=self._clock()),
                label=sensor.name,
                cluster_id=cluster_id,
            )

    # ── Internals ─────────────────────────────────────────────────────────

    def _derive_alarms(
        self, state: DeviceHealthState, *, offline_after_seconds: int | None = None
    ) -> frozenset[HealthAlarm]:
        """Combine adapter-reported alarms with monitor-applied thresholds.

        Battery percent → LOW_BATTERY / BATTERY_CRITICAL.
        ``offline`` flag OR ``last_seen_ts`` older than ``offline_after_seconds`` (the
        irrigator window when omitted) → DEVICE_OFFLINE.
        ``signal_quality`` below threshold → SIGNAL_LOSS.
        """
        stale_after = self._offline_after_seconds if offline_after_seconds is None else offline_after_seconds
        derived: set[HealthAlarm] = set(state.alarms)
        if state.battery_pct is not None:
            if state.battery_pct < self._battery_critical_pct:
                derived.add(HealthAlarm.BATTERY_CRITICAL)
            elif state.battery_pct < self._battery_low_pct:
                derived.add(HealthAlarm.LOW_BATTERY)
        if state.offline:
            derived.add(HealthAlarm.DEVICE_OFFLINE)
        elif state.last_seen_ts is not None:
            now = self._clock()
            if now - state.last_seen_ts > stale_after:
                derived.add(HealthAlarm.DEVICE_OFFLINE)
        if state.signal_quality is not None and state.signal_quality < self._signal_loss_threshold:
            derived.add(HealthAlarm.SIGNAL_LOSS)
        return frozenset(derived)

    def _raise_health_alert(
        self,
        *,
        entity_type: EntityType,
        entity_id: int,
        alarm: HealthAlarm,
        state: DeviceHealthState,
        label: str,
        cluster_id: int | None,
    ) -> bool:
        """Upsert the alarm's alert and push it when this call opened it; False when the write failed."""
        key = health_dedup_key(entity_type, entity_id, alarm)
        severity = alarm_severity(alarm)
        payload = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "alarm": alarm.value,
            "observed_at": state.observed_at,
            "battery_pct": state.battery_pct,
            "signal_quality": state.signal_quality,
            "last_seen_ts": state.last_seen_ts,
            "offline": state.offline,
        }
        try:
            was_open = is_alert_open(self._repo, key)
            alert = self._repo.upsert_alert(
                dedup_key=key,
                source=SOURCE_HEALTH,
                code=alarm.value,
                title=alarm_title(label, alarm),
                message=alarm_message(alarm, label, state),
                severity=severity,
                entity_type=entity_type,
                entity_id=entity_id,
                cluster_id=cluster_id,
                payload=payload,
            )
        except Exception:
            logger.exception("Failed to raise health alert %s for %s %d", alarm.value, entity_type, entity_id)
            return False
        try:
            notify_if_new_alert(self._repo, self._notifier, alert, was_open=was_open)
        except Exception:  # the alert is written; a failed push must not undo that
            logger.warning(
                "Failed to notify health alert %s for %s %d", alarm.value, entity_type, entity_id, exc_info=True
            )
        return True

    def _resolve_health_alert(self, *, entity_type: EntityType, entity_id: int, alarm: HealthAlarm) -> None:
        existing = self._repo.get_open_alert_by_key(health_dedup_key(entity_type, entity_id, alarm))
        if existing is None:
            return
        try:
            self._repo.resolve_alert(existing.id)
        except Exception:
            logger.exception("Failed to resolve health alert %s for %s %d", alarm.value, entity_type, entity_id)


__all__ = [
    "HEALTH_ALARM_TO_TRIGGER",
    "DeviceHealthMonitor",
]
