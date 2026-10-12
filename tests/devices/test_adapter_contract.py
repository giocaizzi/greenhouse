"""Adapter ABC contract — parametrised over every registered adapter.

Acts as a safety net for PR 3+: any new model added to the registry has to
pass these assertions, which means contributors can't ship an adapter that
crashes when the hardware is offline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from fake_data import FAKE_DEVICE_ID
from greenhouse_core.devices import (
    AbstractIrrigatorAdapter,
    AbstractSensorAdapter,
    DeviceGateway,
    DeviceHealthState,
    DeviceRegistry,
    HealthAlarm,
    IK10PWAdapter,
    TR301ZAdapter,
    build_default_registry,
)
from greenhouse_core.models import Irrigator, Sensor

if TYPE_CHECKING:
    from collections.abc import Iterator


def _make_irrigator(model_key: str) -> Irrigator:
    return Irrigator(
        id=42,
        cluster_id=1,
        tuya_device_id=FAKE_DEVICE_ID,
        name="ContractIrrigator",
        type=model_key,
        config={},
    )


def _make_sensor(model_key: str) -> Sensor:
    return Sensor(
        id=42,
        cluster_id=1,
        tuya_device_id=FAKE_DEVICE_ID,
        name="ContractSensor",
        type=model_key,
        plant_id=None,
        config={},
    )


@pytest.fixture
def registry(fake_tuya_env: None) -> Iterator[DeviceRegistry]:
    """Provide the default registry against a mocked Tuya Cloud — no real network.

    ``tinytuya.Cloud`` is patched to a MagicMock so every cloud call inside the one
    shared gateway becomes a no-op.
    """
    del fake_tuya_env  # side-effect fixture (fake Tuya credentials in the environment)
    with patch("greenhouse_core.devices.tinytuya.Cloud") as cloud_class:
        cloud_class.return_value = MagicMock()
        gateway = DeviceGateway()
        yield build_default_registry(gateway)


class TestIrrigatorContract:
    """Every registered irrigator adapter must satisfy the ABC."""

    def test_default_registry_contains_ik10pw(self, registry: DeviceRegistry) -> None:
        """The default registry registers the IK10PW model key."""
        keys = registry.registered_irrigator_keys()
        assert "rainpoint.ik10pw" in keys

    @pytest.mark.parametrize("model_key", ["rainpoint.ik10pw"])
    def test_adapter_is_abstract_subclass(self, registry: DeviceRegistry, model_key: str) -> None:
        """Every registered irrigator adapter implements the abstract adapter contract."""
        adapter = registry.get_irrigator(_make_irrigator(model_key))
        assert isinstance(adapter, AbstractIrrigatorAdapter)
        # specific class survives the lookup
        if model_key == "rainpoint.ik10pw":
            assert isinstance(adapter, IK10PWAdapter)

    @pytest.mark.parametrize("model_key", ["rainpoint.ik10pw"])
    def test_offline_calls_do_not_raise(self, registry: DeviceRegistry, model_key: str) -> None:
        """Hardware-offline calls never raise — they surface offline=True."""
        adapter = registry.get_irrigator(_make_irrigator(model_key))
        irr = _make_irrigator(model_key)

        # ``open_local`` will fail because config has no device_ip — that's
        # the canonical "offline" condition we care about.
        state = adapter.read_health(irr)
        assert isinstance(state, DeviceHealthState)
        assert state.offline is True
        # No alarms — the monitor decides what offline means.
        assert state.alarms == frozenset()

    @pytest.mark.parametrize("model_key", ["rainpoint.ik10pw"])
    def test_health_capabilities_constrain_alarms(self, registry: DeviceRegistry, model_key: str) -> None:
        """``alarms`` is always a subset of ``health_capabilities``."""
        adapter = registry.get_irrigator(_make_irrigator(model_key))
        state = adapter.read_health(_make_irrigator(model_key))
        assert state.alarms <= adapter.health_capabilities

    @pytest.mark.parametrize("model_key", ["rainpoint.ik10pw"])
    def test_ik10pw_health_capabilities(self, registry: DeviceRegistry, model_key: str) -> None:
        """The IK10PW declares the no-water and offline health capabilities."""
        adapter = registry.get_irrigator(_make_irrigator(model_key))
        assert HealthAlarm.NO_WATER in adapter.health_capabilities
        assert HealthAlarm.DEVICE_OFFLINE in adapter.health_capabilities

    @pytest.mark.parametrize("model_key", ["rainpoint.ik10pw"])
    def test_status_returns_dict(self, registry: DeviceRegistry, model_key: str) -> None:
        """``status`` returns a dict for every registered irrigator."""
        adapter = registry.get_irrigator(_make_irrigator(model_key))
        out = adapter.status(_make_irrigator(model_key))
        assert isinstance(out, dict)


class TestSensorContract:
    """Every registered sensor adapter must satisfy the ABC."""

    def test_default_registry_contains_tr301z(self, registry: DeviceRegistry) -> None:
        """The default registry registers the TR301Z model key."""
        keys = registry.registered_sensor_keys()
        assert "tuya.tr301z" in keys

    @pytest.mark.parametrize("model_key", ["tuya.tr301z"])
    def test_adapter_is_abstract_subclass(self, registry: DeviceRegistry, model_key: str) -> None:
        """Every registered sensor adapter implements the abstract adapter contract."""
        adapter = registry.get_sensor(_make_sensor(model_key))
        assert isinstance(adapter, AbstractSensorAdapter)
        if model_key == "tuya.tr301z":
            assert isinstance(adapter, TR301ZAdapter)

    @pytest.mark.parametrize("model_key", ["tuya.tr301z"])
    def test_read_health_no_reading_surfaces_offline(self, registry: DeviceRegistry, model_key: str) -> None:
        """With no persisted reading, health derivation surfaces offline=True."""
        adapter = registry.get_sensor(_make_sensor(model_key))
        assert adapter is not None
        # No live Cloud read — health derives from the latest persisted row,
        # and its absence is the "offline" condition.
        state = adapter.read_health(_make_sensor(model_key), None)
        assert isinstance(state, DeviceHealthState)
        assert state.offline is True
        assert state.alarms <= adapter.health_capabilities

    @pytest.mark.parametrize("model_key", ["tuya.tr301z"])
    def test_tr301z_health_capabilities(self, registry: DeviceRegistry, model_key: str) -> None:
        """The TR301Z declares the battery and offline health capabilities."""
        adapter = registry.get_sensor(_make_sensor(model_key))
        assert adapter is not None
        assert HealthAlarm.LOW_BATTERY in adapter.health_capabilities
        assert HealthAlarm.BATTERY_CRITICAL in adapter.health_capabilities
        assert HealthAlarm.DEVICE_OFFLINE in adapter.health_capabilities
        # B-10: DP 111 water_warning is the soil-dry alert the engine acts on, not a sensor fault.
        assert HealthAlarm.SENSOR_FAULT not in adapter.health_capabilities
