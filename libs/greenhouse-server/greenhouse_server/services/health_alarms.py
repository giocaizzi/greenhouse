"""Inbox wording for device-health alarms: severity, title and message per :class:`HealthAlarm`.

Pure presentation, kept apart from the :mod:`.health_monitor` state machine so the monitor
module holds only transitions, the shared cache and the actuation gate.
"""

from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm

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
