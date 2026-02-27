#!/usr/bin/env python3
"""Test suite for irrigation system - Device management."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fake_data import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FAKE_DEVICE_ID,
    FAKE_DEVICE_IP,
    FAKE_LOCAL_KEY,
    FAKE_REGION,
    FAKE_SENSOR_ID,
)
from tuya_irrigation.devices import TuyaDeviceManager
from tuya_irrigation.models import Irrigator, Sensor


class TestDeviceManager(unittest.TestCase):
    """Test device management with mocked Tuya API."""

    def setUp(self):
        """Set up device manager with fake credentials (no real API calls)."""
        with patch.dict(
            "os.environ",
            {
                "TUYA_CLIENT_ID": FAKE_CLIENT_ID,
                "TUYA_CLIENT_SECRET": FAKE_CLIENT_SECRET,
                "TUYA_REGION": FAKE_REGION,
            },
        ):
            self.dm = TuyaDeviceManager()

    def _make_irrigator(self, device_type="tuya_cloud", config="{}"):
        return Irrigator(
            id=1,
            cluster_id=1,
            tuya_device_id=FAKE_DEVICE_ID,
            name="Test Irrigator",
            type=device_type,
            config=config,
        )

    def _make_sensor(self):
        return Sensor(
            id=1,
            cluster_id=1,
            tuya_device_id=FAKE_SENSOR_ID,
            name="Test Sensor",
            type="temp_humidity",
            config="{}",
        )

    def test_initialization_requires_credentials(self):
        """Device manager raises without Tuya credentials."""
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                TuyaDeviceManager()

    @patch("tuya_irrigation.devices.subprocess.run")
    def test_irrigator_status_parsing(self, mock_run):
        """Device status is correctly parsed from output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="State: ON\nTime remaining: 15 min\nBattery: 80%\n",
            stderr="",
        )
        status = self.dm.irrigator_status(self._make_irrigator())

        self.assertTrue(status.get("running"))
        self.assertEqual(status.get("time_remaining_minutes"), 15)
        self.assertEqual(status.get("battery_percentage"), 80)

    @patch("tuya_irrigation.devices.subprocess.run")
    def test_irrigator_on_command(self, mock_run):
        """Irrigator ON command executes correctly."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        success, _ = self.dm.irrigator_on(self._make_irrigator())
        self.assertTrue(success)
        mock_run.assert_called_once()

    @patch("tuya_irrigation.devices.subprocess.run")
    def test_irrigator_off_command(self, mock_run):
        """Irrigator OFF command executes correctly."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        success, _ = self.dm.irrigator_off(self._make_irrigator())
        self.assertTrue(success)
        mock_run.assert_called_once()

    @patch("tuya_irrigation.devices.subprocess.run")
    def test_irrigator_start_with_duration(self, mock_run):
        """Irrigator START with duration passes correct arguments."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        success, _ = self.dm.irrigator_start(self._make_irrigator(), minutes=5)

        self.assertTrue(success)
        call_args = mock_run.call_args[0][0]
        self.assertIn("--minutes", call_args)
        self.assertIn("5", call_args)

    @patch("tuya_irrigation.devices.subprocess.run")
    def test_sensor_reading_parsing(self, mock_run):
        """Sensor data is correctly parsed from output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Temperature: 22.5°C\nHumidity: 65%\nSoil moisture: 45%\n",
            stderr="",
        )
        data = self.dm.read_sensor(self._make_sensor())

        self.assertAlmostEqual(data.get("temperature"), 22.5)
        self.assertAlmostEqual(data.get("humidity"), 65.0)
        self.assertAlmostEqual(data.get("soil_moisture"), 45.0)

    @patch("tuya_irrigation.devices.subprocess.run")
    def test_device_error_handling(self, mock_run):
        """Device errors are correctly captured."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Connection failed")
        success, output = self.dm.irrigator_on(self._make_irrigator())

        self.assertFalse(success)
        self.assertIn("Connection failed", output)

    @patch("tuya_irrigation.devices.subprocess.run")
    def test_local_mode_device(self, mock_run):
        """Local mode devices use correct commands."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        # RFC 5737: 192.0.2.x is reserved for documentation/testing
        local_config = f'{{"device_ip": "{FAKE_DEVICE_IP}", "local_key": "{FAKE_LOCAL_KEY}"}}'
        success, _ = self.dm.irrigator_on(self._make_irrigator(device_type="tuya_local", config=local_config))

        self.assertTrue(success)
        call_args = mock_run.call_args[0][0]
        self.assertIn("local", call_args)


if __name__ == "__main__":
    unittest.main()
