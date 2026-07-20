"""Generic profile-driven Tuya sensor adapter.

Walks ``profile.dp_parsers`` to convert the raw Tuya properties into the
canonical key set the rest of the codebase consumes
(``temperature``, ``soil_moisture``, ``env_humidity``, ``light``,
``battery_state``…). Per-model adapters only need to swap in their own
parser table via the profile JSON; behaviour stays here.
"""

from __future__ import annotations

import time

from greenhouse_core.devices.gateway import DeviceGateway
from greenhouse_core.devices.health import DeviceHealthState
from greenhouse_core.devices.profile import SensorProfile
from greenhouse_core.devices.sensors.base import AbstractSensorAdapter
from greenhouse_core.models import Sensor, SensorReading


class TuyaSensorAdapter(AbstractSensorAdapter):
    """Default Tuya Cloud sensor driver.

    Profile-driven: ``profile.dp_parsers`` decides which DP codes to
    recognise and how to parse them.
    """

    def __init__(self, profile: SensorProfile, gateway: DeviceGateway):
        self.profile = profile
        self._gateway = gateway

    def read_live(self, sensor: Sensor) -> dict:
        """Read current sensor values via the Tuya Cloud gateway."""
        try:
            return self._gateway.get_live_reading(sensor.tuya_device_id)
        except Exception as e:
            return {"error": str(e)}

    def read_health(self, sensor: Sensor, latest: SensorReading | None = None) -> DeviceHealthState:
        """Default: no health surface beyond reachability.

        Subclasses with battery / water-warning DPs override this to derive
        health from the latest persisted reading.
        """
        return DeviceHealthState(
            observed_at=int(time.time()),
            alarms=frozenset(),
        )
