"""TR-301Z (zwjcy — Tuya 土壤温湿度) soil temp/humidity sensor adapter.

For PR 1 we reuse the parser table that already lives on ``TuyaCloud``
(``cloud.DATAPOINT_PARSERS``). PR 4 will move the parser table into this
module under ``profile.dp_parsers`` and drop the global, but doing so today
would touch the irrigation pipeline. The profile carries only declarative
bits for now.
"""

from __future__ import annotations

import time

from greenhouse_core.cloud import DATAPOINT_PARSERS, TuyaCloud
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.devices.profile import SensorProfile, load_profile_json
from greenhouse_core.devices.sensors.tuya_generic import TuyaSensorAdapter
from greenhouse_core.models import Sensor

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

    health_capabilities = frozenset(
        {
            HealthAlarm.LOW_BATTERY,
            HealthAlarm.BATTERY_CRITICAL,
            HealthAlarm.DEVICE_OFFLINE,
            HealthAlarm.SENSOR_FAULT,
        }
    )

    def __init__(self, cloud: TuyaCloud, profile: SensorProfile | None = None):
        super().__init__(profile or TR301Z_PROFILE, cloud)

    def read_health(self, sensor: Sensor) -> DeviceHealthState:
        """Derive a health snapshot from a live cloud read.

        Maps ``battery_state`` ("low"/"middle"/"high") onto a percentage
        bucket so :class:`DeviceHealthMonitor` can apply the configured
        ``battery_low_pct`` / ``battery_critical_pct`` thresholds uniformly
        across models. ``water_warning`` from DP 111 surfaces as
        :attr:`HealthAlarm.SENSOR_FAULT` — the probe is in soil too dry to
        get a coherent reading, so we treat the live channel as faulted
        for monitoring purposes (the engine doesn't gate on this).
        """
        now = int(time.time())
        try:
            reading = self._cloud.get_live_reading(sensor.tuya_device_id)
        except Exception as exc:
            return DeviceHealthState(
                observed_at=now,
                offline=True,
                alarms=frozenset(),
                raw={"error": f"cloud read failed: {exc}"},
            )

        if not isinstance(reading, dict) or reading.get("error") is not None:
            return DeviceHealthState(
                observed_at=now,
                offline=True,
                alarms=frozenset(),
                raw={"error": reading.get("error") if isinstance(reading, dict) else "non-dict reading"},
            )

        # battery_percentage wins over battery_state when both present.
        battery_pct: int | None = None
        if isinstance(reading.get("battery_percentage"), int):
            battery_pct = int(reading["battery_percentage"])
        else:
            state = reading.get("battery_state")
            if isinstance(state, str):
                battery_pct = _BATTERY_STATE_BUCKETS.get(state.lower())

        alarms: set[HealthAlarm] = set()
        if reading.get("water_warning") is True:
            alarms.add(HealthAlarm.SENSOR_FAULT)

        return DeviceHealthState(
            observed_at=now,
            battery_pct=battery_pct,
            signal_quality=None,
            last_seen_ts=None,
            offline=False,
            alarms=frozenset(alarms),
            raw={
                "battery_state": reading.get("battery_state"),
                "water_warning": reading.get("water_warning"),
            },
        )


__all__ = ["TR301ZAdapter", "TR301Z_PROFILE"]
