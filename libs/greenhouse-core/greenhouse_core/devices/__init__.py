"""Device drivers — registry, profiles, and per-model adapters.

Public surface:

* :class:`DeviceRegistry` — maps DB ``type`` strings to adapter instances.
* :class:`DeviceGateway` — the single Tuya boundary (one Cloud client, cloud
  reads + local-device factory) every adapter shares.
* :func:`build_default_registry` — wires the canonical adapter set against a
  shared :class:`DeviceGateway`.
* :class:`AbstractIrrigatorAdapter` / :class:`AbstractSensorAdapter` — adapter
  ABCs every concrete driver implements.
* Concrete adapters: :class:`IK10PWAdapter`, :class:`TR301ZAdapter`, plus the
  generic Tuya base adapters they extend.

The re-exports resolve lazily on first attribute access (see
:mod:`greenhouse_core._lazy`), so importing a light submodule such as
``devices.health`` does not load every adapter, the gateway and ``tinytuya``.

``tinytuya`` is re-exported from this module so tests that patch
``greenhouse_core.devices.tinytuya.Cloud`` (which mutates the shared
``tinytuya`` module object) intercept construction everywhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from greenhouse_core._lazy import lazy_module_attrs

if TYPE_CHECKING:
    import tinytuya

    from greenhouse_core.devices.gateway import DATAPOINT_PARSERS, DeviceGateway
    from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
    from greenhouse_core.devices.irrigators.base import AbstractIrrigatorAdapter
    from greenhouse_core.devices.irrigators.ik10pw import IK10PW_PROFILE, IK10PWAdapter, alarm_indicates_no_water
    from greenhouse_core.devices.irrigators.tuya_generic import TuyaIrrigatorAdapter
    from greenhouse_core.devices.profile import IrrigatorProfile, SensorProfile
    from greenhouse_core.devices.registry import DeviceRegistry, UnknownDeviceModel
    from greenhouse_core.devices.sensors.base import AbstractSensorAdapter
    from greenhouse_core.devices.sensors.tr301z import TR301Z_PROFILE, TR301ZAdapter
    from greenhouse_core.devices.sensors.tuya_generic import TuyaSensorAdapter

_GATEWAY = "greenhouse_core.devices.gateway"
_IK10PW = "greenhouse_core.devices.irrigators.ik10pw"
_PROFILE = "greenhouse_core.devices.profile"
_REGISTRY = "greenhouse_core.devices.registry"
_TR301Z = "greenhouse_core.devices.sensors.tr301z"
_HEALTH = "greenhouse_core.devices.health"

# Re-exported name -> the module that defines it.
_EXPORTS: dict[str, str] = {
    "DATAPOINT_PARSERS": _GATEWAY,
    "AbstractIrrigatorAdapter": "greenhouse_core.devices.irrigators.base",
    "AbstractSensorAdapter": "greenhouse_core.devices.sensors.base",
    "DeviceGateway": _GATEWAY,
    "DeviceHealthState": _HEALTH,
    "DeviceRegistry": _REGISTRY,
    "HealthAlarm": _HEALTH,
    "IK10PWAdapter": _IK10PW,
    "IK10PW_PROFILE": _IK10PW,
    "IrrigatorProfile": _PROFILE,
    "SensorProfile": _PROFILE,
    "TR301ZAdapter": _TR301Z,
    "TR301Z_PROFILE": _TR301Z,
    "TuyaIrrigatorAdapter": "greenhouse_core.devices.irrigators.tuya_generic",
    "TuyaSensorAdapter": "greenhouse_core.devices.sensors.tuya_generic",
    "UnknownDeviceModel": _REGISTRY,
    "alarm_indicates_no_water": _IK10PW,
}

# Whole modules exposed as attributes: the submodules (listed by ``dir()`` as
# they were when this package imported them all eagerly) and ``tinytuya``.
_MODULES: dict[str, str] = {
    name: f"{__name__}.{name}"
    for name in ("datapoints", "gateway", "health", "irrigators", "profile", "registry", "sensors")
} | {"tinytuya": "tinytuya"}


def build_default_registry(gateway: DeviceGateway) -> DeviceRegistry:
    """Wire the canonical adapter set onto a fresh registry.

    Every adapter shares the one :class:`DeviceGateway` instance — that's
    where the single Tuya Cloud client (and its token) lives. A per-call
    lambda is used as the factory so adapter instances stay cheap to build.
    The adapters are imported here, not at package import, for the same
    reason the re-exports are lazy.
    """
    from greenhouse_core.devices.irrigators.ik10pw import IK10PW_PROFILE, IK10PWAdapter
    from greenhouse_core.devices.registry import DeviceRegistry
    from greenhouse_core.devices.sensors.tr301z import TR301Z_PROFILE, TR301ZAdapter

    registry = DeviceRegistry()
    registry.register_irrigator(
        IK10PW_PROFILE.model_key,
        lambda: IK10PWAdapter(gateway),
    )
    registry.register_sensor(
        TR301Z_PROFILE.model_key,
        lambda: TR301ZAdapter(gateway),
    )
    return registry


__all__ = [
    "DATAPOINT_PARSERS",
    "IK10PW_PROFILE",
    "TR301Z_PROFILE",
    "AbstractIrrigatorAdapter",
    "AbstractSensorAdapter",
    "DeviceGateway",
    "DeviceHealthState",
    "DeviceRegistry",
    "HealthAlarm",
    "IK10PWAdapter",
    "IrrigatorProfile",
    "SensorProfile",
    "TR301ZAdapter",
    "TuyaIrrigatorAdapter",
    "TuyaSensorAdapter",
    "UnknownDeviceModel",
    "alarm_indicates_no_water",
    "build_default_registry",
    "tinytuya",
]

if not TYPE_CHECKING:  # hidden from mypy, so a misspelt attribute stays a type error
    __getattr__, __dir__ = lazy_module_attrs(__name__, globals(), _EXPORTS, _MODULES)
