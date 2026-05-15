"""Canonical device-health surface shared by every adapter.

Every concrete :class:`AbstractIrrigatorAdapter` / :class:`AbstractSensorAdapter`
returns a :class:`DeviceHealthState` from ``read_health``. The
``DeviceHealthMonitor`` service consumes these snapshots, diffs against the
last known state, and raises/resolves typed alerts. Engine code consults the
monitor (never the adapter directly) to decide whether actuation is safe.

Adapters report state; the monitor decides what it means. Battery thresholds,
offline windows, and signal-loss cut-offs all live in
:mod:`greenhouse_core.constants` and are interpreted in the service layer —
adapters stay pure protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class HealthAlarm(StrEnum):
    """Canonical, stable alarm codes shared by every adapter.

    These ride the same plumbing as :class:`~greenhouse_core.logic.decision.TriggerCode`
    so the UI, MCP, and audit log key on a single vocabulary instead of free text.
    ``LOW_BATTERY`` and ``BATTERY_CRITICAL`` are intentionally distinct alarms,
    not severity levels, so the inbox can surface "battery now critical" on top
    of an unresolved "battery low" without merging.
    """

    NO_WATER = "no_water"
    RAIN_DETECTED = "rain_detected"
    LOW_BATTERY = "low_battery"
    BATTERY_CRITICAL = "battery_critical"
    SIGNAL_LOSS = "signal_loss"
    DEVICE_OFFLINE = "device_offline"
    SENSOR_FAULT = "sensor_fault"


@dataclass(frozen=True, slots=True)
class DeviceHealthState:
    """Snapshot of one device's safety/health surface at a point in time.

    Returned by every adapter via ``read_health``. The monitor diffs
    consecutive snapshots to derive transitions; the engine consults the
    monitor (via ``is_actuation_blocked``) before firing the pump.

    Invariants honoured by every adapter:

    * ``observed_at`` is always populated (unix seconds).
    * ``alarms`` only contains codes listed in the adapter's
      ``health_capabilities`` — the monitor relies on this to skip
      unsupported checks for a given model.
    * ``raw`` is debugging-only; the monitor never branches on it.
    """

    observed_at: int
    battery_pct: int | None = None
    signal_quality: int | None = None
    last_seen_ts: int | None = None
    offline: bool = False
    alarms: frozenset[HealthAlarm] = frozenset()
    raw: dict = field(default_factory=dict)


__all__ = ["DeviceHealthState", "HealthAlarm"]
