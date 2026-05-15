"""Abstract base for irrigator adapters.

Each concrete adapter wraps the transport + profile combination for one
irrigator model. The ABC pins the contract every adapter must honour;
``DeviceRegistry`` and tests parametrise over the registered subclasses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.devices.profile import IrrigatorProfile
from greenhouse_core.models import Irrigator


class AbstractIrrigatorAdapter(ABC):
    """Contract for any irrigator driver.

    Methods must be safe to call against offline / unreachable hardware:
    actuation returns ``(False, msg)``, status returns a dict with an
    ``error`` key, and ``read_health`` sets ``offline=True`` rather than
    raising. Raising is reserved for programmer errors (e.g. unknown DP
    request on a model that doesn't declare the capability).
    """

    profile: IrrigatorProfile
    health_capabilities: frozenset[HealthAlarm] = frozenset()

    @abstractmethod
    def start(self, irrigator: Irrigator, minutes: int | None = None) -> tuple[bool, str]:
        """Start irrigation. ``minutes=None`` means "switch on, do not bound the cycle"."""

    @abstractmethod
    def stop(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Stop the irrigator. Idempotent — calling on an already-stopped device is OK."""

    @abstractmethod
    def status(self, irrigator: Irrigator) -> dict:
        """Return a status dict; shape is per-model but always JSON-serialisable."""

    @abstractmethod
    def read_health(self, irrigator: Irrigator) -> DeviceHealthState:
        """Read the current health/safety surface.

        MUST NOT raise on offline / unreachable devices — set
        ``offline=True`` and return. MUST only populate ``alarms`` whose
        codes are in :attr:`health_capabilities`; the monitor relies on
        this invariant to skip unsupported checks for a given model.
        """
