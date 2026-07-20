"""Tests for the IK10PW irrigator adapter and the TR-301Z sensor adapter.

These exercise the adapters directly against the one shared
:class:`DeviceGateway`. Tests patch ``greenhouse_core.devices.tinytuya.Cloud``
because that is how the gateway constructs its single cloud client.
"""

from unittest.mock import MagicMock, patch

import pytest

from fake_data import FAKE_DEVICE_ID
from greenhouse_core.devices import (
    DeviceGateway,
    DeviceRegistry,
    HealthAlarm,
    IK10PWAdapter,
    TR301ZAdapter,
    alarm_indicates_no_water,
    build_default_registry,
)
from greenhouse_core.devices.health import DeviceHealthState
from greenhouse_core.models import Irrigator, Sensor


def _make_irrigator(device_type="tuya_cloud"):
    return Irrigator(
        id=1,
        cluster_id=1,
        tuya_device_id=FAKE_DEVICE_ID,
        name="Test Irrigator",
        type=device_type,
        config={},
    )


def _make_sensor():
    return Sensor(
        id=1,
        cluster_id=1,
        tuya_device_id=FAKE_DEVICE_ID,
        name="Test Sensor",
        type="soil_moisture",
        plant_id=None,
        config={},
    )


@pytest.fixture
def fake_cloud(fake_tuya_env):
    """Patch ``tinytuya.Cloud`` so the gateway never hits a real network."""
    with patch("greenhouse_core.devices.tinytuya.Cloud") as cloud_class:
        cloud = MagicMock()
        cloud_class.return_value = cloud
        yield cloud


@pytest.fixture
def gateway(fake_cloud):
    return DeviceGateway()


@pytest.fixture
def adapter(gateway):
    return IK10PWAdapter(gateway)


@pytest.fixture
def registry(gateway):
    return build_default_registry(gateway)


class TestIK10PWAdapterActuation:
    """The adapter's cloud actuation path delegates to ``transport.send_command``."""

    def test_on_sends_switch_true(self, fake_cloud, adapter):
        fake_cloud.sendcommand.return_value = {"success": True}
        success, msg = adapter.on(_make_irrigator())

        assert success
        assert "ON" in msg
        commands = fake_cloud.sendcommand.call_args[0][1]["commands"]
        assert commands[0]["code"] == "switch"
        assert commands[0]["value"] is True

    def test_off_sends_switch_false(self, fake_cloud, adapter):
        fake_cloud.sendcommand.return_value = {"success": True}
        success, msg = adapter.off(_make_irrigator())

        assert success
        assert "OFF" in msg
        commands = fake_cloud.sendcommand.call_args[0][1]["commands"]
        assert commands[0]["code"] == "switch"
        assert commands[0]["value"] is False

    def test_start_without_duration_just_turns_on(self, fake_cloud, adapter):
        fake_cloud.sendcommand.return_value = {"success": True}
        success, msg = adapter.start(_make_irrigator(), minutes=None)
        assert success
        assert "ON" in msg

    def test_start_with_duration_sets_dp_then_switches_on(self, fake_cloud, adapter):
        """Successful local Duration write → cloud switch ON."""
        fake_cloud.sendcommand.return_value = {"success": True}
        with patch.object(adapter, "_set_duration_local", return_value=(True, "Duration set to 300s")):
            success, msg = adapter.start(_make_irrigator(), minutes=5)

        assert success
        assert "5 min" in msg
        fake_cloud.sendcommand.assert_called_once()

    def test_start_falls_back_to_keepalive_when_local_fails(self, fake_cloud, adapter):
        """Local DP write fails → keep-alive fallback handles the cycle."""
        fake_cloud.sendcommand.return_value = {"success": True}
        with patch.object(adapter, "_set_duration_local", return_value=(False, "Connection refused")):
            with patch.object(
                adapter,
                "_start_keepalive",
                return_value=(True, "Keep-alive completed for 3 min"),
            ):
                success, msg = adapter.start(_make_irrigator(), minutes=3)

        assert success
        assert "3 min" in msg

    def test_actuation_failure_propagates(self, fake_cloud, adapter):
        fake_cloud.sendcommand.return_value = {"success": False, "msg": "Device offline"}
        success, msg = adapter.on(_make_irrigator())

        assert not success
        assert "failed" in msg.lower()

    def test_actuation_network_exception_propagates(self, fake_cloud, adapter):
        fake_cloud.sendcommand.side_effect = ConnectionError("Network unreachable")
        with pytest.raises(ConnectionError):
            adapter.on(_make_irrigator())


class TestIK10PWAdapterStatus:
    """``status`` reads cloud when local isn't reachable."""

    def test_status_parses_cloud_response(self, fake_cloud, adapter):
        fake_cloud.getstatus.return_value = {
            "success": True,
            "result": [
                {"code": "switch", "value": True},
                {"code": "work_state", "value": "watering"},
            ],
        }
        # Force the local path to fail so the adapter falls back to cloud.
        with patch.object(adapter._gateway, "open_local", side_effect=ConnectionError("no route")):
            status = adapter.status(_make_irrigator())

        assert status["running"] is True
        assert status["work_state"] == "watering"


class TestTR301ZAdapterReadLive:
    """The sensor adapter delegates ``read_live`` to TuyaCloud."""

    def test_read_live_returns_parsed_dict(self, registry, fake_cloud):
        adapter = registry.get_sensor(_make_sensor())
        adapter._gateway.get_live_reading = MagicMock(  # type: ignore[attr-defined]
            return_value={"temperature": 22.5, "soil_moisture": 45.0}
        )
        reading = adapter.read_live(_make_sensor())
        assert reading["temperature"] == 22.5
        assert reading["soil_moisture"] == 45.0

    def test_read_live_cloud_failure_returns_error_dict(self, registry):
        adapter = registry.get_sensor(_make_sensor())
        adapter._gateway.get_live_reading = MagicMock(  # type: ignore[attr-defined]
            side_effect=RuntimeError("device offline")
        )
        out = adapter.read_live(_make_sensor())
        assert "error" in out
        assert "device offline" in out["error"]


class TestAlarmParser:
    """alarm_indicates_no_water handles the multiple shapes the DP can take."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, False),
            (0, False),
            (1, True),
            (0x01, True),
            (3, True),  # bitmap with bit 0 set
            (2, False),  # bitmap without bit 0
            (False, False),
            (True, True),
            ("0", False),
            ("1", True),
            ("", False),
            ("garbage", False),
            ({"unexpected": "shape"}, False),
        ],
    )
    def test_alarm_parser(self, value, expected):
        assert alarm_indicates_no_water(value) is expected


class TestIK10PWReadHealth:
    """``read_health`` wraps the local DP 105 read into a :class:`DeviceHealthState`."""

    def _make_local_status(self, *, dps: dict) -> MagicMock:
        fake_local = MagicMock()
        fake_local.status.return_value = {"dps": dps}
        return fake_local

    def test_no_water_alarm_surfaces(self, adapter):
        fake_local = self._make_local_status(dps={"1": True, "104": 42, "105": 1, "106": 2})
        with patch.object(adapter._gateway, "open_local", return_value=fake_local):
            state = adapter.read_health(_make_irrigator())

        assert state.offline is False
        assert HealthAlarm.NO_WATER in state.alarms
        assert state.raw["alarm_raw"] == 1
        assert state.raw["running"] is True
        assert state.raw["left_time"] == 42
        assert state.raw["work_status"] == 2
        assert state.raw["source"] == "local"

    def test_clear_alarm(self, adapter):
        fake_local = self._make_local_status(dps={"1": True, "105": 0})
        with patch.object(adapter._gateway, "open_local", return_value=fake_local):
            state = adapter.read_health(_make_irrigator())

        assert state.offline is False
        assert HealthAlarm.NO_WATER not in state.alarms
        assert state.raw["alarm_raw"] == 0

    def test_missing_dp(self, adapter):
        fake_local = self._make_local_status(dps={"1": True})
        with patch.object(adapter._gateway, "open_local", return_value=fake_local):
            state = adapter.read_health(_make_irrigator())

        assert state.offline is False
        assert HealthAlarm.NO_WATER not in state.alarms
        assert state.raw["alarm_raw"] is None

    def test_local_failure_marks_offline(self, adapter):
        with patch.object(adapter._gateway, "open_local", side_effect=ConnectionError("no route")):
            state = adapter.read_health(_make_irrigator())

        assert state.offline is True
        assert state.alarms == frozenset()
        assert "no route" in state.raw["error"]


class TestRegistry:
    """Smoke checks: the registry yields the expected concrete adapters."""

    def test_registry_resolves_legacy_irrigator_aliases(self, registry: DeviceRegistry):
        for legacy in ("tuya_cloud", "tuya_local", ""):
            adapter = registry.get_irrigator(_make_irrigator(legacy))
            assert isinstance(adapter, IK10PWAdapter)

    def test_registry_resolves_legacy_sensor_aliases(self, registry: DeviceRegistry):
        for legacy in ("soil_moisture", "temp_humidity", "light", ""):
            sensor = Sensor(
                id=1,
                cluster_id=1,
                tuya_device_id=FAKE_DEVICE_ID,
                name="s",
                type=legacy,
                plant_id=None,
                config={},
            )
            adapter = registry.get_sensor(sensor)
            assert isinstance(adapter, TR301ZAdapter)

    def test_health_state_is_typed(self, adapter):
        state = adapter.read_health(_make_irrigator())
        assert isinstance(state, DeviceHealthState)
