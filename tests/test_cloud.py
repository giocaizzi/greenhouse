"""Test suite for Tuya Cloud API client."""

from unittest.mock import MagicMock, patch

import pytest

from fake_data import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REGION, FAKE_SENSOR_ID


class TestTuyaCloud:
    """Test Cloud API client with mocked tinytuya."""

    @pytest.fixture(autouse=True)
    def setup(self):
        with patch("tuya_irrigation.cloud.tinytuya") as mock_tinytuya:
            self.mock_cloud_instance = MagicMock()
            mock_tinytuya.Cloud.return_value = self.mock_cloud_instance

            from tuya_irrigation.cloud import TuyaCloud

            self.cloud = TuyaCloud(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REGION)
            yield

    @patch("tuya_irrigation.cloud.tinytuya")
    @patch.dict("os.environ", {"TUYA_CLIENT_ID": "", "TUYA_CLIENT_SECRET": ""}, clear=False)
    def test_init_requires_credentials(self, _mock_tinytuya):
        """Cloud client raises without credentials."""
        from tuya_irrigation.cloud import TuyaCloud

        with pytest.raises(ValueError):
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

        assert data["temperature"] == pytest.approx(25.1)
        assert data["soil_moisture"] == 45.0
        assert data["battery_state"] == "middle"

    def test_get_live_reading_handles_error(self):
        """Live reading raises on API error."""
        self.mock_cloud_instance.getstatus.return_value = {
            "success": False,
            "msg": "device offline",
        }
        with pytest.raises(RuntimeError):
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

        assert len(logs) == 2
        assert logs[0]["timestamp_ms"] == 1000000
        assert logs[1]["timestamp_ms"] == 2000000
        assert logs[0]["key"] == "temperature"
        assert logs[0]["value"] == pytest.approx(22.0)
        assert logs[1]["key"] == "soil_moisture"
        assert logs[1]["value"] == 50.0

    def test_group_logs_by_timestamp(self):
        """Logs reported within tolerance are grouped into single readings."""
        logs = [
            {"timestamp_ms": 1000, "timestamp": 1, "key": "temperature", "value": 22.0},
            {"timestamp_ms": 2000, "timestamp": 1, "key": "soil_moisture", "value": 45.0},
            {"timestamp_ms": 20000, "timestamp": 20, "key": "temperature", "value": 23.0},
        ]
        grouped = self.cloud.group_logs_by_timestamp(logs, tolerance_ms=5000)

        assert len(grouped) == 2
        assert grouped[0]["temperature"] == pytest.approx(22.0)
        assert grouped[0]["soil_moisture"] == pytest.approx(45.0)
        assert grouped[1]["temperature"] == pytest.approx(23.0)
        assert "soil_moisture" not in grouped[1]

    def test_group_logs_empty(self):
        """Grouping empty logs returns empty list."""
        assert self.cloud.group_logs_by_timestamp([]) == []

    def test_get_live_reading_v2_shadow(self):
        """Live reading uses v2.0 shadow properties when available."""
        self.mock_cloud_instance.cloudrequest.return_value = {
            "success": True,
            "result": {
                "properties": [
                    {"code": "temp_current", "value": 230},
                    {"code": "humidity", "value": 55},
                    {"code": "env_humidity", "value": 72.0},
                    {"code": "water_warning", "value": True},
                ],
            },
        }
        data = self.cloud.get_live_reading(FAKE_SENSOR_ID)

        assert data["temperature"] == pytest.approx(23.0)
        assert data["soil_moisture"] == 55.0
        assert data["env_humidity"] == pytest.approx(72.0)
        assert data["water_warning"] is True
        # Should NOT have called getstatus (v2 succeeded)
        self.mock_cloud_instance.getstatus.assert_not_called()

    def test_get_live_reading_v2_fallback_to_v1(self):
        """Falls back to v1 getstatus when v2 shadow fails."""
        self.mock_cloud_instance.cloudrequest.side_effect = Exception("v2 not available")
        self.mock_cloud_instance.getstatus.return_value = {
            "success": True,
            "result": [
                {"code": "temp_current", "value": 220},
                {"code": "humidity", "value": 50},
            ],
        }
        data = self.cloud.get_live_reading(FAKE_SENSOR_ID)

        assert data["temperature"] == pytest.approx(22.0)
        assert data["soil_moisture"] == 50.0
