"""Test suite for irrigation system - Device management."""

from unittest.mock import MagicMock, patch

import pytest

from fake_data import FAKE_DEVICE_ID
from tuya_irrigation_core.devices import TuyaDeviceManager
from tuya_irrigation_core.models import Irrigator, Sensor


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

    @patch("tuya_irrigation_core.devices.tinytuya.Cloud")
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

    @patch("tuya_irrigation_core.devices.tinytuya.Cloud")
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

    @patch("tuya_irrigation_core.devices.tinytuya.Cloud")
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

    @patch("tuya_irrigation_core.devices.tinytuya.Cloud")
    def test_irrigator_start_without_duration(self, mock_cloud_class, fake_tuya_env):
        """Irrigator START without duration just turns on."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": True}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        success, msg = dm.irrigator_start(_make_irrigator(), minutes=None)

        assert success
        assert "ON" in msg

    @patch("tuya_irrigation_core.devices.tinytuya.Cloud")
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

    @patch("tuya_irrigation_core.devices.tinytuya.Cloud")
    def test_device_error_handling(self, mock_cloud_class, fake_tuya_env):
        """Device errors are correctly captured."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": False, "msg": "Device offline"}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        success, msg = dm.irrigator_on(_make_irrigator())

        assert not success
        assert "failed" in msg.lower()

    @patch("tuya_irrigation_core.cloud.TuyaCloud")
    def test_sensor_reading_parsing(self, mock_cloud_class, fake_tuya_env):
        """Sensor data is correctly parsed via Cloud API."""
        mock_cloud = MagicMock()
        mock_cloud.get_live_reading.return_value = {"temperature": 22.5, "soil_moisture": 45.0}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        reading = dm.read_sensor(_make_sensor())

        assert "temperature" in reading
        assert reading["temperature"] == 22.5
        assert reading["soil_moisture"] == 45.0
