"""Abstract base for sensor adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from greenhouse_core.devices.profile import SensorProfile
from greenhouse_core.models import Sensor


class AbstractSensorAdapter(ABC):
    """Contract for any sensor driver.

    ``read_live`` must not raise on network failure — it returns
    ``{"error": <str>}`` instead. This keeps :class:`SyncService` simple
    (no per-adapter try/except) and uniform across models.
    """

    profile: SensorProfile

    @abstractmethod
    def read_live(self, sensor: Sensor) -> dict:
        """Fetch current values. Returns canonical-key dict, or ``{"error": ...}``."""
