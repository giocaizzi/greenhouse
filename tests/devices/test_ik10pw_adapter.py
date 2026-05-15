"""Tests for the IK10PW irrigator adapter and the legacy ``TuyaDeviceManager`` shim.

The shim is preserved verbatim in PR 1 so existing call sites (server,
deps, pump watcher, bulk) keep working unchanged. Once PR 2 wires the
:class:`DeviceRegistry` through ``IrrigationService`` we'll move the
adapter-level assertions out of the shim and onto the adapter directly.
"""

from unittest.mock import MagicMock, patch

import pytest

from fake_data import FAKE_DEVICE_ID
from greenhouse_core.devices import HealthAlarm, TuyaDeviceManager, alarm_indicates_no_water
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


class TestDeviceManager:
    """Test device management with Cloud API."""

    def test_initialization_requires_credentials(self):
        """Device manager raises without Tuya credentials."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError):
                TuyaDeviceManager()

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_irrigator_on_command(self, mock_cloud_class, fake_tuya_env):
        """Irrigator ON command executes correctly."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": True}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        success, msg = dm.irrigator_on(_make_irrigator())

        assert success
        assert "ON" in msg
        mock_cloud.sendcommand.assert_called_once()
        call_args = mock_cloud.sendcommand.call_args[0]
        assert call_args[0] == FAKE_DEVICE_ID
        commands = call_args[1]["commands"]
        assert commands[0]["code"] == "switch"
        assert commands[0]["value"] is True

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_irrigator_off_command(self, mock_cloud_class, fake_tuya_env):
        """Irrigator OFF command executes correctly."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": True}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        success, msg = dm.irrigator_off(_make_irrigator())

        assert success
        assert "OFF" in msg
        mock_cloud.sendcommand.assert_called_once()
        call_args = mock_cloud.sendcommand.call_args[0]
        commands = call_args[1]["commands"]
        assert commands[0]["code"] == "switch"
        assert commands[0]["value"] is False

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_irrigator_start_with_duration(self, mock_cloud_class, fake_tuya_env):
        """Irrigator START with duration attempts local DP set + cloud switch."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": True}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        # Mock the local duration setter to succeed
        with patch.object(dm, "_set_duration_local", return_value=(True, "Duration set to 300s")):
            success, msg = dm.irrigator_start(_make_irrigator(), minutes=5)

        assert success
        assert "5 min" in msg
        mock_cloud.sendcommand.assert_called_once()

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_irrigator_start_without_duration(self, mock_cloud_class, fake_tuya_env):
        """Irrigator START without duration just turns on."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": True}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        success, msg = dm.irrigator_start(_make_irrigator(), minutes=None)

        assert success
        assert "ON" in msg

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_irrigator_status_parsing(self, mock_cloud_class, fake_tuya_env):
        """Device status is correctly parsed from Cloud API."""
        mock_cloud = MagicMock()
        mock_cloud.getstatus.return_value = {
            "success": True,
            "result": [{"code": "switch", "value": True}, {"code": "work_state", "value": "watering"}],
        }
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        status = dm.irrigator_status(_make_irrigator())

        assert status["running"] is True
        assert status["work_state"] == "watering"

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_device_error_handling(self, mock_cloud_class, fake_tuya_env):
        """Device errors are correctly captured."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": False, "msg": "Device offline"}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        success, msg = dm.irrigator_on(_make_irrigator())

        assert not success
        assert "failed" in msg.lower()

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    @patch("greenhouse_core.cloud.TuyaCloud")
    def test_sensor_reading_parsing(self, mock_cloud_class, _mock_tinytuya_cloud, fake_tuya_env):
        """Sensor data is correctly parsed via Cloud API."""
        mock_cloud = MagicMock()
        mock_cloud.get_live_reading.return_value = {"temperature": 22.5, "soil_moisture": 45.0}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        reading = dm.read_sensor(_make_sensor())

        assert "temperature" in reading
        assert reading["temperature"] == 22.5
        assert reading["soil_moisture"] == 45.0

    # ── Edge case tests ─────────────────────────────────────────────────────

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_irrigator_start_local_failure_cloud_succeeds(self, mock_cloud_class, fake_tuya_env):
        """Local DP set fails but cloud switch succeeds (keepalive fallback)."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": True}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        # Local DP set fails → triggers keepalive fallback
        with patch.object(dm, "_set_duration_local", return_value=(False, "Connection refused")):
            with patch.object(dm, "_irrigator_start_keepalive", return_value=(True, "Keep-alive started for 3 min")):
                success, msg = dm.irrigator_start(_make_irrigator(), minutes=3)

        assert success
        assert "3 min" in msg

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    @patch("greenhouse_core.cloud.TuyaCloud")
    def test_read_sensor_cloud_error_returns_error_dict(self, mock_cloud_class, _mock_tinytuya_cloud, fake_tuya_env):
        """Sensor read returns error dict when cloud API fails."""
        mock_cloud = MagicMock()
        mock_cloud.get_live_reading.side_effect = RuntimeError("device offline")
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        result = dm.read_sensor(_make_sensor())

        assert "error" in result
        assert "device offline" in result["error"]

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_irrigator_on_network_exception(self, mock_cloud_class, fake_tuya_env):
        """Network exception during irrigator_on propagates."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.side_effect = ConnectionError("Network unreachable")
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        with pytest.raises(ConnectionError):
            dm.irrigator_on(_make_irrigator())

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_irrigator_start_zero_minutes(self, mock_cloud_class, fake_tuya_env):
        """Start with minutes=0 is treated as simple ON (no duration)."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": True}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        success, msg = dm.irrigator_start(_make_irrigator(), minutes=0)

        assert success


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


class TestReadIrrigatorHealth:
    """read_irrigator_health wraps the local DP 105 read into a DeviceHealthState."""

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_health_read_no_water(self, mock_cloud_class, fake_tuya_env):
        """Local read with DP 105 == 1 surfaces a NO_WATER alarm."""
        mock_cloud_class.return_value = MagicMock()
        dm = TuyaDeviceManager()
        fake_local = MagicMock()
        fake_local.status.return_value = {
            "dps": {"1": True, "104": 42, "105": 1, "106": 2},
        }
        with patch.object(dm, "_get_local_device", return_value=fake_local):
            state = dm.read_irrigator_health(_make_irrigator())

        assert state.offline is False
        assert HealthAlarm.NO_WATER in state.alarms
        assert state.raw["alarm_raw"] == 1
        assert state.raw["running"] is True
        assert state.raw["left_time"] == 42
        assert state.raw["work_status"] == 2
        assert state.raw["source"] == "local"

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_health_read_clear(self, mock_cloud_class, fake_tuya_env):
        """DP 105 == 0 surfaces an empty alarm set."""
        mock_cloud_class.return_value = MagicMock()
        dm = TuyaDeviceManager()
        fake_local = MagicMock()
        fake_local.status.return_value = {"dps": {"1": True, "105": 0}}
        with patch.object(dm, "_get_local_device", return_value=fake_local):
            state = dm.read_irrigator_health(_make_irrigator())

        assert state.offline is False
        assert HealthAlarm.NO_WATER not in state.alarms
        assert state.raw["alarm_raw"] == 0

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_health_read_missing_dp(self, mock_cloud_class, fake_tuya_env):
        """A device that omits DP 105 surfaces neither offline nor NO_WATER."""
        mock_cloud_class.return_value = MagicMock()
        dm = TuyaDeviceManager()
        fake_local = MagicMock()
        fake_local.status.return_value = {"dps": {"1": True}}
        with patch.object(dm, "_get_local_device", return_value=fake_local):
            state = dm.read_irrigator_health(_make_irrigator())

        assert state.offline is False
        assert HealthAlarm.NO_WATER not in state.alarms
        assert state.raw["alarm_raw"] is None

    @patch("greenhouse_core.devices.tinytuya.Cloud")
    def test_health_read_local_failure(self, mock_cloud_class, fake_tuya_env):
        """Local read failure surfaces offline=True with no alarms."""
        mock_cloud_class.return_value = MagicMock()
        dm = TuyaDeviceManager()
        with patch.object(dm, "_get_local_device", side_effect=ConnectionError("no route")):
            state = dm.read_irrigator_health(_make_irrigator())

        assert state.offline is True
        assert state.alarms == frozenset()
        assert "no route" in state.raw["error"]
