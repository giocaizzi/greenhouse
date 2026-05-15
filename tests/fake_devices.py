"""In-memory fakes for irrigator + sensor adapters.

Use these in server / engine tests to avoid monkey-patching ``tinytuya``.
``FakeIrrigatorAdapter`` records every call so a test can assert
"the engine asked us to start for 5 min" without diving into Tuya internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from greenhouse_core.devices.irrigators.base import AbstractIrrigatorAdapter
from greenhouse_core.devices.profile import IrrigatorProfile, SensorProfile
from greenhouse_core.devices.sensors.base import AbstractSensorAdapter
from greenhouse_core.models import Irrigator, Sensor

FAKE_IRRIGATOR_PROFILE = IrrigatorProfile(
    model_key="fake.irrigator",
    vendor="fake",
    transport="tuya_cloud",
    protocol_version=None,
    dp_map={},
    capabilities=frozenset(),
    duration_unit=None,
    alarm_bitmask=None,
)


FAKE_SENSOR_PROFILE = SensorProfile(
    model_key="fake.sensor",
    vendor="fake",
    transport="tuya_cloud",
    capabilities=frozenset({"reports_soil_moisture", "reports_temperature"}),
    dp_parsers={},
)


@dataclass
class FakeIrrigatorAdapter(AbstractIrrigatorAdapter):
    """In-memory irrigator. Records calls, returns canned results."""

    profile: IrrigatorProfile = field(default=FAKE_IRRIGATOR_PROFILE)
    start_result: tuple[bool, str] = (True, "fake start ok")
    stop_result: tuple[bool, str] = (True, "fake stop ok")
    status_result: dict = field(default_factory=lambda: {"running": False, "source": "fake"})
    alarm_result: dict = field(
        default_factory=lambda: {
            "no_water": False,
            "alarm_raw": None,
            "running": False,
            "left_time": None,
            "work_status": None,
            "source": "fake",
            "error": None,
        }
    )
    calls: list[tuple] = field(default_factory=list)

    def start(self, irrigator: Irrigator, minutes: int | None = None) -> tuple[bool, str]:
        self.calls.append(("start", irrigator.id, minutes))
        return self.start_result

    def stop(self, irrigator: Irrigator) -> tuple[bool, str]:
        self.calls.append(("stop", irrigator.id))
        return self.stop_result

    def status(self, irrigator: Irrigator) -> dict:
        self.calls.append(("status", irrigator.id))
        return dict(self.status_result)

    def read_alarm(self, irrigator: Irrigator) -> dict:
        self.calls.append(("read_alarm", irrigator.id))
        return dict(self.alarm_result)


@dataclass
class FakeSensorAdapter(AbstractSensorAdapter):
    """In-memory sensor. Returns a canned reading and records calls."""

    profile: SensorProfile = field(default=FAKE_SENSOR_PROFILE)
    reading: dict = field(default_factory=lambda: {"temperature": 22.0, "soil_moisture": 50.0})
    calls: list[tuple] = field(default_factory=list)

    def read_live(self, sensor: Sensor) -> dict:
        self.calls.append(("read_live", sensor.id))
        return dict(self.reading)
