"""Abstract base for sensor adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.devices.profile import SensorProfile
from greenhouse_core.models import Sensor, SensorReading


class AbstractSensorAdapter(ABC):
    """Contract for any sensor driver.

    ``read_live`` must not raise on network failure — it returns
    ``{"error": <str>}`` instead. This keeps :class:`SyncService` simple
    (no per-adapter try/except) and uniform across models. ``read_health``
    follows the same rule: ``offline=True`` rather than raising.
    """

    profile: SensorProfile
    health_capabilities: frozenset[HealthAlarm] = frozenset()

    @abstractmethod
    def read_live(self, sensor: Sensor) -> dict:
        """Fetch current values. Returns canonical-key dict, or ``{"error": ...}``."""

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
