#!/usr/bin/env python3
"""Test suite for irrigation system - Device management."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fake_data import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_DEVICE_ID, FAKE_REGION
from tuya_irrigation.devices import TuyaDeviceManager
from tuya_irrigation.models import Irrigator, Sensor


class TestDeviceManager(unittest.TestCase):
    """Test device management with Cloud API."""

    def setUp(self):
        """Mock environment and Cloud API."""
        self.env_patcher = patch.dict(
            "os.environ",
            {
                "TUYA_CLIENT_ID": FAKE_CLIENT_ID,
                "TUYA_CLIENT_SECRET": FAKE_CLIENT_SECRET,
                "TUYA_REGION": FAKE_REGION,
            },
        )
        self.env_patcher.start()

    def tearDown(self):
        """Clean up patches."""
        self.env_patcher.stop()

    def _make_irrigator(self, device_type="tuya_cloud"):
        """Helper to create a fake irrigator."""
        return Irrigator(
            id=1,
            cluster_id=1,
            tuya_device_id=FAKE_DEVICE_ID,
            name="Test Irrigator",
            type=device_type,
            config={},
        )

    def _make_sensor(self):
        """Helper to create a fake sensor."""
        return Sensor(
            id=1,
            cluster_id=1,
            tuya_device_id=FAKE_DEVICE_ID,
            name="Test Sensor",
            type="soil_moisture",
            plant_id=None,
            config={},
        )

    def test_initialization_requires_credentials(self):
        """Device manager raises without Tuya credentials."""
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                TuyaDeviceManager()

    @patch("tuya_irrigation.devices.tinytuya.Cloud")
    def test_irrigator_on_command(self, mock_cloud_class):
        """Irrigator ON command executes correctly."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": True}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        success, msg = dm.irrigator_on(self._make_irrigator())

        self.assertTrue(success)
        self.assertIn("ON", msg)
        mock_cloud.sendcommand.assert_called_once()
        call_args = mock_cloud.sendcommand.call_args[0]
        self.assertEqual(call_args[0], FAKE_DEVICE_ID)
        commands = call_args[1]["commands"]
        self.assertEqual(commands[0]["code"], "switch")
        self.assertTrue(commands[0]["value"])

    @patch("tuya_irrigation.devices.tinytuya.Cloud")
    def test_irrigator_off_command(self, mock_cloud_class):
        """Irrigator OFF command executes correctly."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": True}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        success, msg = dm.irrigator_off(self._make_irrigator())

        self.assertTrue(success)
        self.assertIn("OFF", msg)
        mock_cloud.sendcommand.assert_called_once()
        call_args = mock_cloud.sendcommand.call_args[0]
        commands = call_args[1]["commands"]
        self.assertEqual(commands[0]["code"], "switch")
        self.assertFalse(commands[0]["value"])

    @patch("tuya_irrigation.devices.tinytuya.Cloud")
    def test_irrigator_start_with_duration(self, mock_cloud_class):
        """Irrigator START with duration passes correct arguments."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": True}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        success, msg = dm.irrigator_start(self._make_irrigator(), minutes=5)

        self.assertTrue(success)
        self.assertIn("5 minutes", msg)
        mock_cloud.sendcommand.assert_called_once()
        call_args = mock_cloud.sendcommand.call_args[0]
        commands = call_args[1]["commands"]
        # Should have switch ON + countdown
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0]["code"], "switch")
        self.assertTrue(commands[0]["value"])
        self.assertEqual(commands[1]["code"], "countdown_1")
        self.assertEqual(commands[1]["value"], 300)  # 5 minutes = 300 seconds

    @patch("tuya_irrigation.devices.tinytuya.Cloud")
    def test_irrigator_status_parsing(self, mock_cloud_class):
        """Device status is correctly parsed from Cloud API."""
        mock_cloud = MagicMock()
        mock_cloud.getstatus.return_value = {
            "success": True,
            "result": [{"code": "switch", "value": True}, {"code": "work_state", "value": "watering"}],
        }
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        status = dm.irrigator_status(self._make_irrigator())

        self.assertTrue(status["running"])
        self.assertEqual(status["work_state"], "watering")

    @patch("tuya_irrigation.devices.tinytuya.Cloud")
    def test_device_error_handling(self, mock_cloud_class):
        """Device errors are correctly captured."""
        mock_cloud = MagicMock()
        mock_cloud.sendcommand.return_value = {"success": False, "msg": "Device offline"}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        success, msg = dm.irrigator_on(self._make_irrigator())

        self.assertFalse(success)
        self.assertIn("failed", msg.lower())

    @patch("tuya_irrigation.cloud.TuyaCloud")
    def test_sensor_reading_parsing(self, mock_cloud_class):
        """Sensor data is correctly parsed via Cloud API."""
        mock_cloud = MagicMock()
        mock_cloud.get_live_reading.return_value = {"temperature": 22.5, "soil_moisture": 45.0}
        mock_cloud_class.return_value = mock_cloud

        dm = TuyaDeviceManager()
        reading = dm.read_sensor(self._make_sensor())

        self.assertIn("temperature", reading)
        self.assertEqual(reading["temperature"], 22.5)
        self.assertEqual(reading["soil_moisture"], 45.0)


if __name__ == "__main__":
    unittest.main()
