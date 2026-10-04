"""Abstract base for sensor adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.devices.profile import SensorProfile
from greenhouse_core.models import Sensor, SensorReading


class AbstractSensorAdapter(ABC):
    """Contract for any sensor driver: derive health from persisted readings.

    Live reads do not go through the adapter — the sync job reads the Cloud
    via :meth:`DeviceGateway.get_live_reading` directly. ``read_health`` must
    not raise on an offline or unreachable device; it reports
    ``offline=True`` instead, so the health poll needs no per-adapter
    try/except.
    """

    profile: SensorProfile
    health_capabilities: frozenset[HealthAlarm] = frozenset()

    @abstractmethod
    def read_health(self, sensor: Sensor, latest: SensorReading | None = None) -> DeviceHealthState:
        """Derive the current health/safety surface.

        ``latest`` is the sensor's most recent persisted :class:`SensorReading`
        (the sync job is the sole Cloud writer of those rows). Adapters derive
        health from it rather than issuing their own live Cloud read, so the
        slow health poll costs zero Cloud calls. MUST NOT raise on
        offline/unreachable devices, and MUST only populate ``alarms`` whose
        codes are in :attr:`health_capabilities`.
        """
