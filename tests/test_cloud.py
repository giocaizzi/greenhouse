#!/usr/bin/env python3
"""Test suite for Tuya Cloud API client."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fake_data import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REGION, FAKE_SENSOR_ID


class TestTuyaCloud(unittest.TestCase):
    """Test Cloud API client with mocked tinytuya."""

    @patch("tuya_irrigation.cloud.tinytuya")
    def setUp(self, mock_tinytuya):
        """Set up cloud client with mocked tinytuya."""
        self.mock_cloud_instance = MagicMock()
        mock_tinytuya.Cloud.return_value = self.mock_cloud_instance

        from tuya_irrigation.cloud import TuyaCloud

        self.cloud = TuyaCloud(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REGION)

    @patch("tuya_irrigation.cloud.tinytuya")
    @patch.dict("os.environ", {"TUYA_CLIENT_ID": "", "TUYA_CLIENT_SECRET": ""}, clear=False)
    def test_init_requires_credentials(self, _mock_tinytuya):
        """Cloud client raises without credentials."""
        from tuya_irrigation.cloud import TuyaCloud

        with self.assertRaises(ValueError):
            TuyaCloud("", "", "eu")

    def test_get_live_reading_parses_soil_sensor(self):
        """Live reading correctly parses TR-301Z soil sensor DPs."""
        self.mock_cloud_instance.getstatus.return_value = {
            "success": True,
            "result": [
                {"code": "temp_current", "value": 251},
                {"code": "humidity", "value": 45},
                {"code": "battery_state", "value": "middle"},
            ],
        }
        data = self.cloud.get_live_reading(FAKE_SENSOR_ID)

        self.assertAlmostEqual(data["temperature"], 25.1)
        self.assertEqual(data["soil_moisture"], 45.0)
        self.assertEqual(data["battery_state"], "middle")

    def test_get_live_reading_handles_error(self):
        """Live reading raises on API error."""
        self.mock_cloud_instance.getstatus.return_value = {
            "success": False,
            "msg": "device offline",
        }
        with self.assertRaises(RuntimeError):
            self.cloud.get_live_reading(FAKE_SENSOR_ID)

    def test_get_device_logs_parses_and_sorts(self):
        """Device logs are parsed and sorted chronologically."""
        self.mock_cloud_instance.getdevicelog.return_value = {
            "result": {
                "logs": [
                    {"code": "humidity", "value": "50", "event_time": 2000000},
                    {"code": "temp_current", "value": "220", "event_time": 1000000},
                ],
            },
        }
        logs = self.cloud.get_device_logs(FAKE_SENSOR_ID, hours=1)

        self.assertEqual(len(logs), 2)
        # Sorted chronologically (oldest first)
        self.assertEqual(logs[0]["timestamp_ms"], 1000000)
        self.assertEqual(logs[1]["timestamp_ms"], 2000000)
        # Parsed values
        self.assertEqual(logs[0]["key"], "temperature")
        self.assertAlmostEqual(logs[0]["value"], 22.0)
        self.assertEqual(logs[1]["key"], "soil_moisture")
        self.assertEqual(logs[1]["value"], 50.0)

    def test_group_logs_by_timestamp(self):
        """Logs reported within tolerance are grouped into single readings."""
        logs = [
            {"timestamp_ms": 1000, "timestamp": 1, "key": "temperature", "value": 22.0},
            {"timestamp_ms": 2000, "timestamp": 1, "key": "soil_moisture", "value": 45.0},
            # 10 seconds later — same group (within 5s tolerance)
            {"timestamp_ms": 20000, "timestamp": 20, "key": "temperature", "value": 23.0},
        ]
        grouped = self.cloud.group_logs_by_timestamp(logs, tolerance_ms=5000)

        self.assertEqual(len(grouped), 2)
        # First group has both temp and soil
        self.assertAlmostEqual(grouped[0]["temperature"], 22.0)
        self.assertAlmostEqual(grouped[0]["soil_moisture"], 45.0)
        # Second group has only temp
        self.assertAlmostEqual(grouped[1]["temperature"], 23.0)
        self.assertNotIn("soil_moisture", grouped[1])

    def test_group_logs_empty(self):
        """Grouping empty logs returns empty list."""
        self.assertEqual(self.cloud.group_logs_by_timestamp([]), [])


if __name__ == "__main__":
    unittest.main()
