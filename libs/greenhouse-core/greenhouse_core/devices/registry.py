"""Device registry — maps DB ``type`` strings to adapter factories.

Resolution policy (deliberately strict for irrigators, lenient for sensors):

* Unknown irrigator ``model_key`` → raise :class:`UnknownDeviceModel`. We
  refuse to actuate hardware we don't have an adapter for.
* Unknown sensor ``model_key`` → log a warning and return ``None``. A bad
  sensor just degrades the cluster to weather-only operation; the engine
  already handles that.

Rows are matched on their exact ``type`` (the ``vendor.model`` key). The legacy
column values (``tuya_cloud`` / ``tuya_local``, ``soil_moisture`` /
``temp_humidity`` / ``light``) are no longer aliased, because Alembic
revisions ``6c9d4e2f3a12`` and ``a1d3f5b7c902`` rewrite them (and ``""``) on upgrade; a row
written with one afterwards is an unknown model — the irrigator is refused (logged, never actuated) and the sensor is
skipped by the health poll. Fix such a row by setting its type to the model key.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from greenhouse_core.devices.irrigators.base import AbstractIrrigatorAdapter
from greenhouse_core.devices.sensors.base import AbstractSensorAdapter
from greenhouse_core.models import Irrigator, Sensor

logger = logging.getLogger(__name__)

IrrigatorFactory = Callable[[], AbstractIrrigatorAdapter]
SensorFactory = Callable[[], AbstractSensorAdapter]


class UnknownDeviceModel(Exception):
    """Raised when an irrigator references a model not in the registry."""


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
        factory = self._irrigators.get(irrigator.type)
        if factory is None:
            msg = f"No adapter registered for irrigator type {irrigator.type!r} (known: {_known(self._irrigators)})"
            logger.error("%s; irrigator %s will not be actuated", msg, irrigator.id)
            raise UnknownDeviceModel(msg)
        return factory()

    def get_sensor(self, sensor: Sensor) -> AbstractSensorAdapter | None:
        """Return the adapter for ``sensor``, or ``None`` if the model is unknown.

        Logs a warning on miss instead of raising — unknown sensor models
        degrade the system, they don't endanger hardware.
        """
        factory = self._sensors.get(sensor.type)
        if factory is None:
            logger.warning(
                "No adapter registered for sensor type %r (known: %s); sensor %s will be skipped",
                sensor.type,
                _known(self._sensors),
                sensor.id,
            )
            return None
        return factory()

    # Introspection — useful for the parametrised adapter contract test.

    def registered_irrigator_keys(self) -> tuple[str, ...]:
        """The irrigator model keys, in registration order."""
        return tuple(self._irrigators)

    def registered_sensor_keys(self) -> tuple[str, ...]:
        """The sensor model keys, in registration order."""
        return tuple(self._sensors)


def _known(factories: Mapping[str, object]) -> str:
    """The registered model keys, sorted, for a lookup-miss message."""
    return ", ".join(sorted(factories)) or "none"
