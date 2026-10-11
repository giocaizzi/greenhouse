"""TR-301Z (zwjcy — Tuya 土壤温湿度) soil temp/humidity sensor adapter.

The profile's ``dp_parsers`` are selected from the gateway's single
``DATAPOINT_PARSERS`` table (the one home for Tuya DP → canonical-key parsing).
``read_health`` derives its snapshot from the sensor's latest persisted
reading rather than a live Cloud read, so the slow health poll costs no quota.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from greenhouse_core.devices.gateway import DATAPOINT_PARSERS, DeviceGateway
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.devices.profile import SensorProfile, load_profile_json
from greenhouse_core.devices.sensors.tuya_generic import TuyaSensorAdapter

if TYPE_CHECKING:
    from greenhouse_core.models import Sensor, SensorReading

# Battery-state enum buckets reported by the TR-301Z firmware. Adapters
# never read project-wide thresholds — they convert the device's coarse
# enum to a percentage and let DeviceHealthMonitor decide what "low" means
# (battery_low_pct / battery_critical_pct live in constants.py).
_BATTERY_STATE_BUCKETS = {
    "low": 10,
    "middle": 50,
    "high": 90,
}


def _load_profile() -> SensorProfile:
    raw = load_profile_json("tr301z.json")
    parsers = {code: DATAPOINT_PARSERS[code] for code in raw.get("_parser_codes", []) if code in DATAPOINT_PARSERS}
    return SensorProfile(
        model_key=raw["model_key"],
        vendor=raw["vendor"],
        transport=raw["transport"],
        capabilities=frozenset(raw.get("capabilities", [])),
        dp_parsers=parsers,
    )


TR301Z_PROFILE = _load_profile()


class TR301ZAdapter(TuyaSensorAdapter):
    """Adapter for the TR-301Z soil temperature + humidity probe."""

    # No SENSOR_FAULT: the only candidate DP, ``water_warning`` (DP 111), is the
    # device's own soil-dry alert — the decision engine's WATER_WARNING irrigation
    # trigger — not a probe fault (B-10).
    health_capabilities = frozenset(
        {
            HealthAlarm.LOW_BATTERY,
            HealthAlarm.BATTERY_CRITICAL,
            HealthAlarm.DEVICE_OFFLINE,
        }
    )

    def __init__(self, gateway: DeviceGateway, profile: SensorProfile | None = None):
        super().__init__(profile or TR301Z_PROFILE, gateway)

    def read_health(self, sensor: Sensor, latest: SensorReading | None = None) -> DeviceHealthState:
        """Derive a health snapshot from the latest persisted reading.

        No live Cloud read: the sync job is the sole Cloud writer of sensor
        readings, and battery / recency are both present on the row it
        persists. Maps ``battery_state`` ("low"/"middle"/"high") onto a
        percentage bucket so :class:`DeviceHealthMonitor` applies its
        ``battery_low_pct`` / ``battery_critical_pct`` thresholds uniformly.
        ``water_warning`` (DP 111) is the device's soil-dry alert, which the
        decision engine acts on (WATER_WARNING → irrigate); it is a soil
        condition, not a device-health alarm, so it only appears in ``raw``.
        ``last_seen_ts`` is the reading timestamp, so the monitor derives
        :attr:`HealthAlarm.DEVICE_OFFLINE` from staleness (``OFFLINE_AFTER_MINUTES``)
        without any device round-trip. A missing row reads as offline.
        """
        now = int(time.time())
        if latest is None:
            return DeviceHealthState(
                observed_at=now,
                offline=True,
                alarms=frozenset(),
                raw={"reason": "no persisted reading"},
            )

        battery_pct: int | None = None
        state = latest.battery_state
        if isinstance(state, str):
            battery_pct = _BATTERY_STATE_BUCKETS.get(state.lower())

        return DeviceHealthState(
            observed_at=now,
            battery_pct=battery_pct,
            signal_quality=None,
            last_seen_ts=latest.timestamp,
            offline=False,
            alarms=frozenset(),
            raw={
                "battery_state": latest.battery_state,
                "water_warning": latest.water_warning,
                "reading_ts": latest.timestamp,
            },
        )


__all__ = ["TR301Z_PROFILE", "TR301ZAdapter"]
