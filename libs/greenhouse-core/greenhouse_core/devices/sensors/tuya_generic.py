"""Generic profile-driven Tuya sensor adapter.

Live reads do not go through the adapter: the sync job calls
:meth:`DeviceGateway.get_live_reading`, which converts the raw Tuya properties
into the canonical key set the rest of the codebase consumes
(``temperature``, ``soil_moisture``, ``env_humidity``, ``light``,
``battery_state``…) through the gateway's single ``DATAPOINT_PARSERS`` table.
Per-model adapters describe their DP codes in the profile JSON and override
``read_health`` where the model reports health DPs.
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

    Parsing lives in the gateway (``DATAPOINT_PARSERS``); ``profile.dp_parsers``
    only records which DP codes the model reports.
    """

    def __init__(self, profile: SensorProfile, gateway: DeviceGateway):
        self.profile = profile
        self._gateway = gateway

    def read_health(self, sensor: Sensor, latest: SensorReading | None = None) -> DeviceHealthState:
        """Default: no health surface beyond reachability.

        Subclasses with battery / water-warning DPs override this to derive
        health from the latest persisted reading.
        """
        return DeviceHealthState(
            observed_at=int(time.time()),
            alarms=frozenset(),
        )
