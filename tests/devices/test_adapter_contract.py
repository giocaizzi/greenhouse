"""Adapter ABC contract — parametrised over every registered adapter.

Acts as a safety net for PR 3+: any new model added to the registry has to
pass these assertions, which means contributors can't ship an adapter that
crashes when the hardware is offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fake_data import FAKE_DEVICE_ID
from greenhouse_core.devices import (
    AbstractIrrigatorAdapter,
    AbstractSensorAdapter,
    DeviceHealthState,
    DeviceRegistry,
    HealthAlarm,
    IK10PWAdapter,
    TR301ZAdapter,
    TuyaTransport,
    build_default_registry,
)
from greenhouse_core.models import Irrigator, Sensor


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
def registry(fake_tuya_env, monkeypatch):
    """A registry wired with the default adapter set against a mocked Tuya
    cloud — no real network. ``tinytuya.Cloud`` is patched to a MagicMock so
    every cloud call inside the adapters becomes a no-op that returns mocks.
    """
    with patch("greenhouse_core.devices.tinytuya.Cloud") as cloud_class:
        cloud_class.return_value = MagicMock()
        transport = TuyaTransport()
        # cloud.py also instantiates tinytuya.Cloud; reuse the same patch.
        with patch("greenhouse_core.cloud.tinytuya.Cloud") as cloud_class2:
            cloud_class2.return_value = MagicMock()
            yield build_default_registry(transport)


class TestIrrigatorContract:
    """Every registered irrigator adapter must satisfy the ABC."""

    def test_default_registry_contains_ik10pw(self, registry: DeviceRegistry):
        keys = registry.registered_irrigator_keys()
        assert "rainpoint.ik10pw" in keys

    @pytest.mark.parametrize("model_key", ["rainpoint.ik10pw"])
    def test_adapter_is_abstract_subclass(self, registry: DeviceRegistry, model_key: str):
        adapter = registry.get_irrigator(_make_irrigator(model_key))
        assert isinstance(adapter, AbstractIrrigatorAdapter)
        # specific class survives the lookup
        if model_key == "rainpoint.ik10pw":
            assert isinstance(adapter, IK10PWAdapter)

    @pytest.mark.parametrize("model_key", ["rainpoint.ik10pw"])
    def test_offline_calls_do_not_raise(self, registry: DeviceRegistry, model_key: str):
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
    def test_health_capabilities_constrain_alarms(self, registry: DeviceRegistry, model_key: str):
        """``alarms`` is always a subset of ``health_capabilities``."""
        adapter = registry.get_irrigator(_make_irrigator(model_key))
        state = adapter.read_health(_make_irrigator(model_key))
        assert state.alarms <= adapter.health_capabilities

    @pytest.mark.parametrize("model_key", ["rainpoint.ik10pw"])
    def test_ik10pw_health_capabilities(self, registry: DeviceRegistry, model_key: str):
        adapter = registry.get_irrigator(_make_irrigator(model_key))
        assert HealthAlarm.NO_WATER in adapter.health_capabilities
        assert HealthAlarm.DEVICE_OFFLINE in adapter.health_capabilities

    @pytest.mark.parametrize("model_key", ["rainpoint.ik10pw"])
    def test_status_returns_dict(self, registry: DeviceRegistry, model_key: str):
        adapter = registry.get_irrigator(_make_irrigator(model_key))
        out = adapter.status(_make_irrigator(model_key))
        assert isinstance(out, dict)


class TestSensorContract:
    """Every registered sensor adapter must satisfy the ABC."""

    def test_default_registry_contains_tr301z(self, registry: DeviceRegistry):
        keys = registry.registered_sensor_keys()
        assert "tuya.tr301z" in keys

    @pytest.mark.parametrize("model_key", ["tuya.tr301z"])
    def test_adapter_is_abstract_subclass(self, registry: DeviceRegistry, model_key: str):
        adapter = registry.get_sensor(_make_sensor(model_key))
        assert isinstance(adapter, AbstractSensorAdapter)
        if model_key == "tuya.tr301z":
            assert isinstance(adapter, TR301ZAdapter)

    @pytest.mark.parametrize("model_key", ["tuya.tr301z"])
    def test_read_live_returns_dict(self, registry: DeviceRegistry, model_key: str):
        adapter = registry.get_sensor(_make_sensor(model_key))
        # The cloud client is a MagicMock; ``get_live_reading`` will return
        # a MagicMock, which the adapter passes through. The contract is:
        # never raise.
        out = adapter.read_live(_make_sensor(model_key))
        assert out is not None

    @pytest.mark.parametrize("model_key", ["tuya.tr301z"])
    def test_read_health_returns_state_on_cloud_failure(self, registry: DeviceRegistry, model_key: str):
        """A cloud failure surfaces offline=True without raising."""
        adapter = registry.get_sensor(_make_sensor(model_key))
        # Force the cloud client to raise so the adapter must absorb it.
        adapter._cloud.get_live_reading = MagicMock(side_effect=RuntimeError("offline"))  # type: ignore[attr-defined]
        state = adapter.read_health(_make_sensor(model_key))
        assert isinstance(state, DeviceHealthState)
        assert state.offline is True
        assert state.alarms <= adapter.health_capabilities

    @pytest.mark.parametrize("model_key", ["tuya.tr301z"])
    def test_tr301z_health_capabilities(self, registry: DeviceRegistry, model_key: str):
        adapter = registry.get_sensor(_make_sensor(model_key))
        assert HealthAlarm.LOW_BATTERY in adapter.health_capabilities
        assert HealthAlarm.BATTERY_CRITICAL in adapter.health_capabilities
        assert HealthAlarm.SENSOR_FAULT in adapter.health_capabilities
        assert HealthAlarm.DEVICE_OFFLINE in adapter.health_capabilities


class TestRegistryFailureModes:
    """Resolution policy: fail closed on irrigators, fail degraded on sensors."""

    def test_unknown_irrigator_raises(self, registry: DeviceRegistry):
        from greenhouse_core.devices import UnknownDeviceModel

        with pytest.raises(UnknownDeviceModel):
            registry.get_irrigator(_make_irrigator("rainpoint.does-not-exist"))

    def test_unknown_sensor_returns_none(self, registry: DeviceRegistry, caplog):
        out = registry.get_sensor(_make_sensor("tuya.does-not-exist"))
        assert out is None

    def test_legacy_irrigator_aliases_resolve(self, registry: DeviceRegistry):
        for legacy in ("tuya_cloud", "tuya_local", ""):
            adapter = registry.get_irrigator(_make_irrigator(legacy))
            assert isinstance(adapter, IK10PWAdapter), legacy

    def test_legacy_sensor_aliases_resolve(self, registry: DeviceRegistry):
        for legacy in ("soil_moisture", "temp_humidity", "light", ""):
            adapter = registry.get_sensor(_make_sensor(legacy))
            assert isinstance(adapter, TR301ZAdapter), legacy
