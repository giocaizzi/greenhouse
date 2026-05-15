"""Abstract base for irrigator adapters.

Each concrete adapter wraps the transport + profile combination for one
irrigator model. The ABC pins the contract every adapter must honour;
``DeviceRegistry`` and tests parametrise over the registered subclasses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from greenhouse_core.devices.profile import IrrigatorProfile
from greenhouse_core.models import Irrigator


class AbstractIrrigatorAdapter(ABC):
    """Contract for any irrigator driver.

    Methods must be safe to call against offline / unreachable hardware:
    they return ``(False, msg)`` or a status dict with an ``error`` /
    ``no_water=None`` field rather than raising. Raising is reserved for
    programmer errors (e.g. unknown DP request on a model that doesn't
    declare the capability).
    """

    profile: IrrigatorProfile

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
    def read_alarm(self, irrigator: Irrigator) -> dict:
        """Return the dry-run / fault state.

        Result dict always contains ``no_water`` (``bool | None`` — ``None``
        when the read failed) and ``error`` (``str | None``). Models without
        a water-shortage DP report ``no_water=False`` and a stable shape so
        the pump watcher can treat them uniformly.
        """
