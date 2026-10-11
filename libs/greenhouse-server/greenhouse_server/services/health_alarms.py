"""Device-health alarm vocabulary: trigger codes, dedup keys, inbox wording and inbox lookups.

Kept apart from the :mod:`.health_monitor` state machine so the monitor module holds only
transitions, the shared cache and the actuation gate.
"""

import logging
from dataclasses import dataclass

from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.logic.decision import TriggerCode
from greenhouse_core.models import ENTITY_IRRIGATOR, ENTITY_SENSOR, SOURCE_HEALTH, EntityType
from greenhouse_core.repository import IrrigationRepository

logger = logging.getLogger("greenhouse_server.services.health_monitor")

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

# Message per alarm; ``{label}`` is the device name, ``{pct}`` its battery percent.
_ALARM_MESSAGES: dict[HealthAlarm, str] = {
    HealthAlarm.NO_WATER: "'{label}' reported water shortage. Pump actuation will be blocked until refilled.",
    HealthAlarm.RAIN_DETECTED: "'{label}' reports rain. Skipping scheduled irrigation.",
    HealthAlarm.LOW_BATTERY: "'{label}' battery is low ({pct}%). Replace soon to keep readings flowing.",
    HealthAlarm.BATTERY_CRITICAL: "'{label}' battery is critical ({pct}%). Replace immediately.",
    HealthAlarm.SIGNAL_LOSS: "'{label}' signal quality is degraded — readings may be intermittent.",
    HealthAlarm.DEVICE_OFFLINE: "'{label}' is unreachable. Actuation is blocked until the device comes back online.",
    HealthAlarm.SENSOR_FAULT: "'{label}' is reporting a sensor fault. Cross-check the probe placement.",
}


def alarm_severity(alarm: HealthAlarm) -> str:
    """Inbox severity of an alarm (``warning`` for one without an entry)."""
    return _ALARM_SEVERITY.get(alarm, "warning")


def alarm_title(entity_label: str, alarm: HealthAlarm) -> str:
    """``"<Alarm Name> · <device>"``."""
    pretty = alarm.value.replace("_", " ").title()
    return f"{pretty} · {entity_label}"


def alarm_message(alarm: HealthAlarm, label: str, state: DeviceHealthState) -> str:
    """The alert body for ``alarm`` on the device ``label``."""
    template = _ALARM_MESSAGES.get(alarm, "'{label}' raised " + alarm.value + ".")
    return template.format(label=label, pct=state.battery_pct)


def is_low_battery_state(raw: object) -> bool:
    """True when a persisted ``battery_state`` string indicates low charge."""
    if not isinstance(raw, str):
        return False
    return raw.strip().lower() == "low"


def health_dedup_key(entity_type: EntityType, entity_id: int, alarm: HealthAlarm) -> str:
    """Single source of truth for health-alert dedup keys."""
    return f"health:{entity_type}:{entity_id}:{alarm.value}"


def infer_cluster_id(repo: IrrigationRepository, entity_type: EntityType, entity_id: int) -> int | None:
    """The cluster an irrigator or sensor belongs to, or None."""
    if entity_type == ENTITY_IRRIGATOR:
        irr = repo.get_irrigator(entity_id)
        return irr.cluster_id if irr else None
    if entity_type == ENTITY_SENSOR:
        sensor = repo.get_sensor(entity_id)
        return sensor.cluster_id if sensor else None
    return None


def infer_label(repo: IrrigationRepository, entity_type: EntityType, entity_id: int) -> str | None:
    """The name of an irrigator or sensor, or None."""
    if entity_type == ENTITY_IRRIGATOR:
        irr = repo.get_irrigator(entity_id)
        return irr.name if irr else None
    if entity_type == ENTITY_SENSOR:
        sensor = repo.get_sensor(entity_id)
        return sensor.name if sensor else None
    return None


def unresolved_alarms_by_entity(repo: IrrigationRepository) -> dict[tuple[str, int], frozenset[HealthAlarm]]:
    """The alarms each device still has an unresolved health alert for (the B-9 cache rebuild).

    Alerts whose code this version does not know, or that name no device, are skipped.
    """
    found: dict[tuple[str, int], set[HealthAlarm]] = {}
    for alert in repo.list_unresolved_alerts(SOURCE_HEALTH):
        alarm = _known_alarm(alert.code)
        if alarm is None or alert.entity_id is None or alert.entity_type not in (ENTITY_IRRIGATOR, ENTITY_SENSOR):
            logger.debug("Not restoring health alert %d (%s)", alert.id, alert.dedup_key)
            continue
        found.setdefault((alert.entity_type, alert.entity_id), set()).add(alarm)
    return {key: frozenset(alarms) for key, alarms in found.items()}


def _known_alarm(code: str) -> HealthAlarm | None:
    """The :class:`HealthAlarm` an alert code names, or None for a code this version does not know."""
    try:
        return HealthAlarm(code)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class AlarmThresholds:
    """The monitor-applied cut-offs (adapters report raw values; the monitor decides what is low)."""

    battery_low_pct: int
    battery_critical_pct: int
    signal_loss_threshold: int


def derive_alarms(
    state: DeviceHealthState, thresholds: AlarmThresholds, *, stale_after_seconds: int, now: int
) -> frozenset[HealthAlarm]:
    """Combine adapter-reported alarms with the monitor's thresholds.

    Battery percent → LOW_BATTERY / BATTERY_CRITICAL. ``offline`` flag OR ``last_seen_ts`` older
    than ``stale_after_seconds`` → DEVICE_OFFLINE. ``signal_quality`` below threshold → SIGNAL_LOSS.
    """
    derived: set[HealthAlarm] = set(state.alarms)
    if state.battery_pct is not None:
        if state.battery_pct < thresholds.battery_critical_pct:
            derived.add(HealthAlarm.BATTERY_CRITICAL)
        elif state.battery_pct < thresholds.battery_low_pct:
            derived.add(HealthAlarm.LOW_BATTERY)
    if state.offline or (state.last_seen_ts is not None and now - state.last_seen_ts > stale_after_seconds):
        derived.add(HealthAlarm.DEVICE_OFFLINE)
    if state.signal_quality is not None and state.signal_quality < thresholds.signal_loss_threshold:
        derived.add(HealthAlarm.SIGNAL_LOSS)
    return frozenset(derived)
