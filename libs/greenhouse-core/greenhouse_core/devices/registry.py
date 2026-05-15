"""Device registry — maps DB ``type`` strings to adapter factories.

Resolution policy (deliberately strict for irrigators, lenient for sensors):

* Unknown irrigator ``model_key`` → raise :class:`UnknownDeviceModel`. We
  refuse to actuate hardware we don't have an adapter for.
* Unknown sensor ``model_key`` → log a warning and return ``None``. A bad
  sensor just degrades the cluster to weather-only operation; the engine
  already handles that.

Legacy column values (``"tuya_cloud"`` / ``"tuya_local"`` for irrigators,
``"soil_moisture"`` / ``"temp_humidity"`` / ``"light"`` for sensors) are
honoured via :attr:`LEGACY_IRRIGATOR_ALIASES` / :attr:`LEGACY_SENSOR_ALIASES`
so existing rows keep working before the data migration has run and so PR 1
remains a strict no-op for callers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from greenhouse_core.devices.irrigators.base import AbstractIrrigatorAdapter
from greenhouse_core.devices.sensors.base import AbstractSensorAdapter
from greenhouse_core.models import Irrigator, Sensor

logger = logging.getLogger(__name__)

IrrigatorFactory = Callable[[], AbstractIrrigatorAdapter]
SensorFactory = Callable[[], AbstractSensorAdapter]


class UnknownDeviceModel(Exception):
    """Raised when an irrigator references a model not in the registry."""


# Legacy DB values predate the move to ``vendor.model`` keys. Both forms
# resolve to the same adapter so PR 1 can land without touching data.
LEGACY_IRRIGATOR_ALIASES: dict[str, str] = {
    "tuya_cloud": "rainpoint.ik10pw",
    "tuya_local": "rainpoint.ik10pw",
    "": "rainpoint.ik10pw",
}

LEGACY_SENSOR_ALIASES: dict[str, str] = {
    "soil_moisture": "tuya.tr301z",
    "temp_humidity": "tuya.tr301z",
    "light": "tuya.tr301z",
    "": "tuya.tr301z",
}


class DeviceRegistry:
    """Resolves DB device rows to adapter instances.

    Adapter factories are zero-arg callables; the caller closes over any
    transport / cloud instances they need. Factories are invoked on every
    lookup — adapters are expected to be cheap to construct and to share
    underlying transport state through closures.
    """

    def __init__(self) -> None:
        self._irrigators: dict[str, IrrigatorFactory] = {}
        self._sensors: dict[str, SensorFactory] = {}

    # ── Registration ──────────────────────────────────────────────────────

    def register_irrigator(self, model_key: str, factory: IrrigatorFactory) -> None:
        """Register an irrigator adapter factory under ``model_key``."""
        self._irrigators[model_key] = factory

    def register_sensor(self, model_key: str, factory: SensorFactory) -> None:
        """Register a sensor adapter factory under ``model_key``."""
        self._sensors[model_key] = factory

    # ── Lookup ────────────────────────────────────────────────────────────

    def get_irrigator(self, irrigator: Irrigator) -> AbstractIrrigatorAdapter:
        """Return the adapter for ``irrigator``. Raises ``UnknownDeviceModel`` on miss."""
        key = self._resolve_irrigator_key(irrigator.type or "")
        factory = self._irrigators.get(key)
        if factory is None:
            raise UnknownDeviceModel(f"No adapter registered for irrigator type {irrigator.type!r} (resolved={key!r})")
        return factory()

    def get_sensor(self, sensor: Sensor) -> AbstractSensorAdapter | None:
        """Return the adapter for ``sensor``, or ``None`` if the model is unknown.

        Logs a warning on miss instead of raising — unknown sensor models
        degrade the system, they don't endanger hardware.
        """
        key = self._resolve_sensor_key(sensor.type or "")
        factory = self._sensors.get(key)
        if factory is None:
            logger.warning(
                "No adapter registered for sensor type %r (resolved=%r); sensor will be skipped",
                sensor.type,
                key,
            )
            return None
        return factory()

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_irrigator_key(raw: str) -> str:
        return LEGACY_IRRIGATOR_ALIASES.get(raw, raw)

    @staticmethod
    def _resolve_sensor_key(raw: str) -> str:
        return LEGACY_SENSOR_ALIASES.get(raw, raw)

    # Introspection — useful for the parametrised adapter contract test.

    def registered_irrigator_keys(self) -> tuple[str, ...]:
        return tuple(self._irrigators)

    def registered_sensor_keys(self) -> tuple[str, ...]:
        return tuple(self._sensors)
