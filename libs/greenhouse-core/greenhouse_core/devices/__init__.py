"""Device drivers — registry, profiles, and per-model adapters.

Public surface:

* :class:`DeviceRegistry` — maps DB ``type`` strings to adapter instances.
* :func:`build_default_registry` — wires the canonical adapter set against a
  shared :class:`TuyaTransport`.
* :class:`AbstractIrrigatorAdapter` / :class:`AbstractSensorAdapter` — adapter
  ABCs every concrete driver implements.
* Concrete adapters: :class:`IK10PWAdapter`, :class:`TR301ZAdapter`, plus the
  generic Tuya base adapters they extend.

The legacy :class:`TuyaDeviceManager` shim was removed in PR 2 once
:class:`IrrigationService`, :class:`SyncService`, :class:`PumpWatcherService`,
and the routes layer were all migrated to the registry. ``tinytuya`` is still
re-exported from this module so tests that patch
``greenhouse_core.devices.tinytuya.Cloud`` continue to work without rewriting.
"""

from __future__ import annotations

import tinytuya  # re-exported for tests that patch greenhouse_core.devices.tinytuya.Cloud

from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.devices.irrigators.base import AbstractIrrigatorAdapter
from greenhouse_core.devices.irrigators.ik10pw import IK10PW_PROFILE, IK10PWAdapter, alarm_indicates_no_water
from greenhouse_core.devices.irrigators.tuya_generic import TuyaIrrigatorAdapter
from greenhouse_core.devices.profile import IrrigatorProfile, SensorProfile
from greenhouse_core.devices.registry import DeviceRegistry, UnknownDeviceModel
from greenhouse_core.devices.sensors.base import AbstractSensorAdapter
from greenhouse_core.devices.sensors.tr301z import TR301Z_PROFILE, TR301ZAdapter
from greenhouse_core.devices.sensors.tuya_generic import TuyaSensorAdapter
from greenhouse_core.devices.tuya_transport import TuyaTransport


def build_default_registry(transport: TuyaTransport) -> DeviceRegistry:
    """Wire the canonical adapter set onto a fresh registry.

    Adapters share the same :class:`TuyaTransport` instance — that's where
    the Tuya Cloud credentials live. A per-call lambda is used as the
    factory so adapter instances stay cheap to construct.
    """
    registry = DeviceRegistry()
    registry.register_irrigator(
        IK10PW_PROFILE.model_key,
        lambda: IK10PWAdapter(transport),
    )
    # Lazy cloud import — TuyaSensorAdapter wants a TuyaCloud, not a
    # TuyaTransport. Keep them separate but co-located here.
    from greenhouse_core.cloud import TuyaCloud

    cloud = TuyaCloud(transport.client_id, transport.client_secret, transport.region)
    registry.register_sensor(
        TR301Z_PROFILE.model_key,
        lambda: TR301ZAdapter(cloud),
    )
    return registry


__all__ = [
    "AbstractIrrigatorAdapter",
    "AbstractSensorAdapter",
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
    "TuyaTransport",
    "UnknownDeviceModel",
    "alarm_indicates_no_water",
    "build_default_registry",
    "tinytuya",
]
