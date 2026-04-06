"""Test suite for statistics and reporting."""

import time

import pytest

from fake_data import FAKE_CLUSTER_NAME, FAKE_DEVICE_ID, FAKE_IRRIGATOR_NAME
from tuya_irrigation_core.stats import export_csv, format_duration, get_irrigation_stats


class TestFormatDuration:
    def test_minutes_only(self):
        assert format_duration(30) == "30min"

    def test_hours_and_minutes(self):
        assert format_duration(90) == "1h 30min"

    def test_exact_hours(self):
        assert format_duration(120) == "2h"

    def test_zero(self):
        assert format_duration(0) == "0min"


class TestGetIrrigationStats:
    def test_no_irrigators(self, tmp_db):
        """Returns error when no irrigators in cluster."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        stats = get_irrigation_stats(tmp_db, cluster_id)
        assert "error" in stats

    def test_stats_with_events(self, tmp_db):
        """Stats correctly aggregate irrigation events."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        irrigator_id = tmp_db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name=FAKE_IRRIGATOR_NAME,
            irrigator_type="tuya_cloud",
            config={},
        )

        now = int(time.time())
        tmp_db.add_irrigation_event(
            irrigator_id=irrigator_id,
            action="start",
            triggered_by="auto",
            duration_minutes=3,
            timestamp=now - 3600,
        )
        tmp_db.add_irrigation_event(
            irrigator_id=irrigator_id,
            action="start",
            triggered_by="manual",
            duration_minutes=5,
            timestamp=now - 7200,
        )

        stats = get_irrigation_stats(tmp_db, cluster_id, days=1)
        assert stats["total_events"] == 2
        assert stats["total_duration_minutes"] == 8
        assert len(stats["irrigations"]) == 2
        assert stats["avg_duration_minutes"] == pytest.approx(4.0)

    def test_stats_empty_period(self, tmp_db):
        """Stats with no events in period returns zero counts."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        tmp_db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name=FAKE_IRRIGATOR_NAME,
            irrigator_type="tuya_cloud",
            config={},
        )

        stats = get_irrigation_stats(tmp_db, cluster_id, days=1)
        assert stats["total_events"] == 0
        assert stats["total_duration_minutes"] == 0


class TestExportCsv:
    def test_export_creates_file(self, tmp_db, tmp_path):
        """CSV export creates a valid file."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        irrigator_id = tmp_db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name=FAKE_IRRIGATOR_NAME,
            irrigator_type="tuya_cloud",
            config={},
        )

        now = int(time.time())
        tmp_db.add_irrigation_event(
            irrigator_id=irrigator_id,
            action="start",
            triggered_by="auto",
            duration_minutes=3,
            timestamp=now,
        )

        csv_path = str(tmp_path / "test_export.csv")
        export_csv(tmp_db, cluster_id, days=1, output_path=csv_path)

        with open(csv_path) as f:
            lines = f.readlines()
        assert len(lines) == 2  # Header + 1 row
        assert "timestamp" in lines[0]
        assert FAKE_IRRIGATOR_NAME in lines[1]
