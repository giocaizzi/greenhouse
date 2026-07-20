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
  :meth:`is_actuation_blocked` so the engine doesn't re-poll the hardware;
* a current ``repo`` reference, swapped per scheduler job tick via
  :meth:`bind_repo` so background jobs that open their own sessions can
  reuse a long-lived monitor instance without leaking the cache.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from greenhouse_core.constants import (
    BATTERY_CRITICAL_PCT,
    BATTERY_LOW_PCT,
    OFFLINE_AFTER_MINUTES,
    SENSOR_HEALTH_BACKFILL_WINDOW,
    SIGNAL_LOSS_THRESHOLD,
)
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.logic.decision import TriggerCode
from greenhouse_core.models import ENTITY_IRRIGATOR, ENTITY_SENSOR, Irrigator, Sensor
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import notify_if_new_alert
from greenhouse_server.services.notify import NtfyClient

logger = logging.getLogger(__name__)

# Alert source for every health-derived alert. Distinct from
# ``SOURCE_PUMP`` so the pump-watcher's fast-path alarms (still routed
# through the monitor) collapse onto the same dedup_key scheme as the
# slow-path monitor, while the audit log can still tell who raised them.
SOURCE_HEALTH = "health"

# Legacy alert code raised by the original PumpWatcher implementation.
# Carried here so :meth:`migrate_legacy_pump_alerts` can resolve open rows
# on startup once the new ``health:`` dedup_key takes over.
LEGACY_PUMP_DRY_RUN_CODE = "pump_dry_run"


HEALTH_ALARM_TO_TRIGGER: dict[HealthAlarm, TriggerCode] = {
    HealthAlarm.NO_WATER: TriggerCode.DEVICE_NO_WATER,
    HealthAlarm.RAIN_DETECTED: TriggerCode.DEVICE_RAIN_DETECTED,
    HealthAlarm.LOW_BATTERY: TriggerCode.DEVICE_BATTERY_LOW,
    HealthAlarm.BATTERY_CRITICAL: TriggerCode.DEVICE_BATTERY_CRITICAL,
    HealthAlarm.SIGNAL_LOSS: TriggerCode.DEVICE_SIGNAL_LOSS,
    HealthAlarm.DEVICE_OFFLINE: TriggerCode.DEVICE_OFFLINE,
    # SENSOR_FAULT is sensor-only and advisory; the engine does not gate
    # on it and so it has no TriggerCode counterpart.
}


# Severity per alarm — drives the inbox styling and ack/resolve UX.
_ALARM_SEVERITY: dict[HealthAlarm, str] = {
    HealthAlarm.NO_WATER: "critical",
    HealthAlarm.RAIN_DETECTED: "info",
    HealthAlarm.LOW_BATTERY: "warning",
    HealthAlarm.BATTERY_CRITICAL: "critical",
    HealthAlarm.SIGNAL_LOSS: "warning",
    HealthAlarm.DEVICE_OFFLINE: "warning",
    HealthAlarm.SENSOR_FAULT: "warning",
}


def _alarm_title(entity_label: str, alarm: HealthAlarm) -> str:
    pretty = alarm.value.replace("_", " ").title()
    return f"{pretty} · {entity_label}"


def _dedup_key(entity_type: str, entity_id: int, alarm: HealthAlarm) -> str:
    """Single source of truth for health-alert dedup keys."""
    return f"health:{entity_type}:{entity_id}:{alarm.value}"


@dataclass(frozen=True)
class _Cached:
    """Per-entity cached state used for transition diffing + actuation gating."""

    state: DeviceHealthState
    derived_alarms: frozenset[HealthAlarm]


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
        signal_loss_threshold: int = SIGNAL_LOSS_THRESHOLD,
        clock: Callable[[], int] = lambda: int(time.time()),
        notifier: NtfyClient | None = None,
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._notifier = notifier
        self._battery_low_pct = battery_low_pct
        self._battery_critical_pct = battery_critical_pct
        self._offline_after_seconds = offline_after_minutes * 60
        self._signal_loss_threshold = signal_loss_threshold
        self._clock = clock
        self._cache: dict[tuple[str, int], _Cached] = {}

    def bind_repo(self, repo: IrrigationRepository) -> None:
        """Swap the repository this monitor writes through.

        Scheduler jobs open their own DB sessions per tick; the cache
        lives on the singleton monitor so this is the only piece of state
        that needs to change between ticks.
        """
        self._repo = repo

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

    # ── Recording (shared with PumpWatcher) ───────────────────────────────

    def record(
        self,
        entity_type: str,
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
        derived = self._derive_alarms(state)
        key = (entity_type, entity_id)
        previous = self._cache.get(key)
        prev_alarms = previous.derived_alarms if previous else frozenset()

        appeared = derived - prev_alarms
        disappeared = prev_alarms - derived

        cluster_id = cluster_id or self._infer_cluster_id(entity_type, entity_id)
        label = label or self._infer_label(entity_type, entity_id) or f"{entity_type}#{entity_id}"

        for alarm in appeared:
            self._raise_health_alert(
                entity_type=entity_type,
                entity_id=entity_id,
                alarm=alarm,
                state=state,
                label=label,
                cluster_id=cluster_id,
            )
        for alarm in disappeared:
            self._resolve_health_alert(entity_type=entity_type, entity_id=entity_id, alarm=alarm)

        self._cache[key] = _Cached(state=state, derived_alarms=derived)
        return derived

    # ── Engine gate ───────────────────────────────────────────────────────

    def is_actuation_blocked(self, irrigator: Irrigator) -> tuple[bool, list[HealthAlarm]]:
        """Should the engine hold off actuating this irrigator?

        Reads the cached derived alarms only — never re-polls the device.
        Returns the list of blocking alarms so the caller can build a
        :class:`Reason` for the decision audit trail.
        """
        cached = self._cache.get((ENTITY_IRRIGATOR, irrigator.id))
        if cached is None:
            return False, []
        blocking = [
            alarm
            for alarm in cached.derived_alarms
            if alarm in (HealthAlarm.NO_WATER, HealthAlarm.RAIN_DETECTED, HealthAlarm.DEVICE_OFFLINE)
        ]
        return bool(blocking), blocking

    # ── Backfill from SensorReading history (startup hook) ────────────────

    def backfill_from_history(self, *, window: int = SENSOR_HEALTH_BACKFILL_WINDOW) -> None:
        """Raise low-battery / sensor-fault alerts derived from recent readings.

        Only persistent signals are back-filled — transient ones (offline,
        signal-loss) must come from a live read. Prevents the server
        forgetting a known-bad battery state across a restart.
        """
        for sensor in self._repo.list_all_sensors():
            readings = self._repo.get_recent_readings(sensor.id, hours=24 * 7)
            if not readings:
                continue
            recent = readings[:window]
            if len(recent) < window:
                continue
            cluster_id = sensor.cluster_id

            if all(_is_low_battery_state(r.battery_state) for r in recent):
                key = _dedup_key(ENTITY_SENSOR, sensor.id, HealthAlarm.LOW_BATTERY)
                if not self._repo.session.scalar(self._open_alert_stmt(key)):
                    self._raise_health_alert(
                        entity_type=ENTITY_SENSOR,
                        entity_id=sensor.id,
                        alarm=HealthAlarm.LOW_BATTERY,
                        state=DeviceHealthState(observed_at=self._clock()),
                        label=sensor.name,
                        cluster_id=cluster_id,
                    )

            if all(r.water_warning is True for r in recent):
                key = _dedup_key(ENTITY_SENSOR, sensor.id, HealthAlarm.SENSOR_FAULT)
                if not self._repo.session.scalar(self._open_alert_stmt(key)):
                    self._raise_health_alert(
                        entity_type=ENTITY_SENSOR,
                        entity_id=sensor.id,
                        alarm=HealthAlarm.SENSOR_FAULT,
                        state=DeviceHealthState(observed_at=self._clock()),
                        label=sensor.name,
                        cluster_id=cluster_id,
                    )

    # ── Legacy alias migration (startup hook) ─────────────────────────────

    def migrate_legacy_pump_alerts(self) -> int:
        """Resolve open ``pump_dry_run`` alerts so the new ``health:`` key takes over.

        PR 1.5 unifies the dedup_key for pump dry-run from
        ``pump::pump_dry_run::…`` to ``health:irrigator:{id}:no_water``.
        Without this migration a restart would surface both rows in the
        inbox until the next live trip resolves the legacy one.
        """
        from sqlalchemy import select

        from greenhouse_core.models import Alert

        count = 0
        stmt = select(Alert).where(Alert.code == LEGACY_PUMP_DRY_RUN_CODE, Alert.status != "resolved")
        for alert in self._repo.session.scalars(stmt):
            self._repo.resolve_alert(alert.id)
            count += 1
        return count

    # ── Internals ─────────────────────────────────────────────────────────

    def _derive_alarms(self, state: DeviceHealthState) -> frozenset[HealthAlarm]:
        """Combine adapter-reported alarms with monitor-applied thresholds.

        Battery percent → LOW_BATTERY / BATTERY_CRITICAL.
        ``offline`` flag OR stale ``last_seen_ts`` → DEVICE_OFFLINE.
        ``signal_quality`` below threshold → SIGNAL_LOSS.
        """
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
            if now - state.last_seen_ts > self._offline_after_seconds:
                derived.add(HealthAlarm.DEVICE_OFFLINE)
        if state.signal_quality is not None and state.signal_quality < self._signal_loss_threshold:
            derived.add(HealthAlarm.SIGNAL_LOSS)
        return frozenset(derived)

    def _raise_health_alert(
        self,
        *,
        entity_type: str,
        entity_id: int,
        alarm: HealthAlarm,
        state: DeviceHealthState,
        label: str,
        cluster_id: int | None,
    ) -> None:
        key = _dedup_key(entity_type, entity_id, alarm)
        severity = _ALARM_SEVERITY.get(alarm, "warning")
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
            alert = self._repo.upsert_alert(
                dedup_key=key,
                source=SOURCE_HEALTH,
                code=alarm.value,
                title=_alarm_title(label, alarm),
                message=self._alarm_message(alarm, label, state),
                severity=severity,
                entity_type=entity_type,
                entity_id=entity_id,
                cluster_id=cluster_id,
                payload=payload,
            )
            notify_if_new_alert(self._repo, self._notifier, alert)
        except Exception:
            logger.exception("Failed to raise health alert %s for %s %d", alarm.value, entity_type, entity_id)

    def _resolve_health_alert(self, *, entity_type: str, entity_id: int, alarm: HealthAlarm) -> None:
        key = _dedup_key(entity_type, entity_id, alarm)
        stmt = self._open_alert_stmt(key)
        existing = self._repo.session.scalar(stmt)
        if existing is None:
            return
        try:
            self._repo.resolve_alert(existing.id)
        except Exception:
            logger.exception("Failed to resolve health alert %s for %s %d", alarm.value, entity_type, entity_id)

    @staticmethod
    def _open_alert_stmt(dedup_key: str):
        from sqlalchemy import select

        from greenhouse_core.models import Alert

        return select(Alert).where(Alert.dedup_key == dedup_key, Alert.status != "resolved")

    @staticmethod
    def _alarm_message(alarm: HealthAlarm, label: str, state: DeviceHealthState) -> str:
        if alarm is HealthAlarm.NO_WATER:
            return f"'{label}' reported water shortage. Pump actuation will be blocked until refilled."
        if alarm is HealthAlarm.RAIN_DETECTED:
            return f"'{label}' reports rain. Skipping scheduled irrigation."
        if alarm is HealthAlarm.LOW_BATTERY:
            pct = state.battery_pct
            return f"'{label}' battery is low ({pct}%). Replace soon to keep readings flowing."
        if alarm is HealthAlarm.BATTERY_CRITICAL:
            pct = state.battery_pct
            return f"'{label}' battery is critical ({pct}%). Replace immediately."
        if alarm is HealthAlarm.SIGNAL_LOSS:
            return f"'{label}' signal quality is degraded — readings may be intermittent."
        if alarm is HealthAlarm.DEVICE_OFFLINE:
            return f"'{label}' is unreachable. Actuation is blocked until the device comes back online."
        if alarm is HealthAlarm.SENSOR_FAULT:
            return f"'{label}' is reporting a sensor fault. Cross-check the probe placement."
        return f"'{label}' raised {alarm.value}."

    def _infer_cluster_id(self, entity_type: str, entity_id: int) -> int | None:
        if entity_type == ENTITY_IRRIGATOR:
            irr = self._repo.get_irrigator(entity_id)
            return irr.cluster_id if irr else None
        if entity_type == ENTITY_SENSOR:
            sensor = self._repo.get_sensor(entity_id)
            return sensor.cluster_id if sensor else None
        return None

    def _infer_label(self, entity_type: str, entity_id: int) -> str | None:
        if entity_type == ENTITY_IRRIGATOR:
            irr = self._repo.get_irrigator(entity_id)
            return irr.name if irr else None
        if entity_type == ENTITY_SENSOR:
            sensor = self._repo.get_sensor(entity_id)
            return sensor.name if sensor else None
        return None


def _is_low_battery_state(raw: object) -> bool:
    """True when a persisted ``battery_state`` string indicates low charge."""
    if not isinstance(raw, str):
        return False
    return raw.strip().lower() == "low"


__all__ = [
    "DeviceHealthMonitor",
    "HEALTH_ALARM_TO_TRIGGER",
    "LEGACY_PUMP_DRY_RUN_CODE",
    "SOURCE_HEALTH",
]
