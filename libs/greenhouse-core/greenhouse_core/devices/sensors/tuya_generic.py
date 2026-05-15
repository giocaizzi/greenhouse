"""Generic profile-driven Tuya sensor adapter.

Walks ``profile.dp_parsers`` to convert the raw Tuya properties into the
canonical key set the rest of the codebase consumes
(``temperature``, ``soil_moisture``, ``env_humidity``, ``light``,
``battery_state``…). Per-model adapters only need to swap in their own
parser table via the profile JSON; behaviour stays here.
"""

from __future__ import annotations

import time

from greenhouse_core.cloud import TuyaCloud
from greenhouse_core.devices.health import DeviceHealthState
from greenhouse_core.devices.profile import SensorProfile
from greenhouse_core.devices.sensors.base import AbstractSensorAdapter
from greenhouse_core.models import Sensor


class TuyaSensorAdapter(AbstractSensorAdapter):
    """Default Tuya Cloud sensor driver.

    Profile-driven: ``profile.dp_parsers`` decides which DP codes to
    recognise and how to parse them.
    """

    def __init__(self, profile: SensorProfile, cloud: TuyaCloud):
        self.profile = profile
        self._cloud = cloud

    def read_live(self, sensor: Sensor) -> dict:
        """Read current sensor values via Tuya Cloud.

        Delegates to :meth:`TuyaCloud.get_live_reading`, which already walks
        the parser table — keeping the call here means PR 4 can swap the
        flat global table for ``self.profile.dp_parsers`` without touching
        the irrigation pipeline.
        """
        try:
            return self._cloud.get_live_reading(sensor.tuya_device_id)
        except Exception as e:
            return {"error": str(e)}

    def read_health(self, sensor: Sensor) -> DeviceHealthState:
        """Default: no health surface beyond reachability.

        Subclasses with battery / water-warning DPs override this to do a
        real read.
        """
        return DeviceHealthState(
            observed_at=int(time.time()),
            alarms=frozenset(),
        )
