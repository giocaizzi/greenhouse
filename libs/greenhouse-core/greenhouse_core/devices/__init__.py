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

``tinytuya`` is re-exported from this module so tests that patch
``greenhouse_core.devices.tinytuya.Cloud`` (which mutates the shared
``tinytuya`` module object) intercept construction everywhere.
"""

from __future__ import annotations

import tinytuya  # re-exported for tests that patch greenhouse_core.devices.tinytuya.Cloud

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


def build_default_registry(gateway: DeviceGateway) -> DeviceRegistry:
    """Wire the canonical adapter set onto a fresh registry.

    Every adapter shares the one :class:`DeviceGateway` instance — that's
    where the single Tuya Cloud client (and its token) lives. A per-call
    lambda is used as the factory so adapter instances stay cheap to build.
    """
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
    "AbstractIrrigatorAdapter",
    "AbstractSensorAdapter",
    "DeviceGateway",
    "DeviceHealthState",
    "DeviceRegistry",
    "HealthAlarm",
    "IK10PWAdapter",
    "IK10PW_PROFILE",
    "IrrigatorProfile",
    "SensorProfile",
    "TR301ZAdapter",
    "TR301Z_PROFILE",
    "TuyaIrrigatorAdapter",
    "TuyaSensorAdapter",
    "UnknownDeviceModel",
    "alarm_indicates_no_water",
    "build_default_registry",
    "tinytuya",
]
