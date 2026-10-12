"""Test suite for statistics and reporting."""

import time

import pytest

from fake_data import FAKE_CLUSTER_NAME, FAKE_DEVICE_ID, FAKE_IRRIGATOR_NAME
from greenhouse_core.models import EventAction, TriggeredBy
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.stats import get_irrigation_stats

EVENTS_LOGGED = 2
TOTAL_MINUTES = 3 + 5  # the two logged runs


class TestGetIrrigationStats:
    """``get_irrigation_stats`` over seeded events."""

    def test_no_irrigators(self, tmp_db: IrrigationRepository) -> None:
        """Returns error when no irrigators in cluster."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        stats = get_irrigation_stats(tmp_db, cluster_id)
        assert "error" in stats

    def test_stats_with_events(self, tmp_db: IrrigationRepository) -> None:
        """Stats correctly aggregate irrigation events."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        irrigator_id = tmp_db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name=FAKE_IRRIGATOR_NAME,
            irrigator_type="rainpoint.ik10pw",
            config={},
        )

        now = int(time.time())
        tmp_db.add_irrigation_event(
            irrigator_id=irrigator_id,
            action=EventAction.START,
            triggered_by=TriggeredBy.AUTO,
            duration_minutes=3,
            timestamp=now - 3600,
        )
        tmp_db.add_irrigation_event(
            irrigator_id=irrigator_id,
            action=EventAction.START,
            triggered_by=TriggeredBy.MANUAL,
            duration_minutes=5,
            timestamp=now - 7200,
        )

        stats = get_irrigation_stats(tmp_db, cluster_id, days=1)
        assert "error" not in stats
        assert stats["total_events"] == EVENTS_LOGGED
        assert stats["total_duration_minutes"] == TOTAL_MINUTES
        assert len(stats["irrigations"]) == EVENTS_LOGGED
        assert stats["avg_duration_minutes"] == pytest.approx(4.0)

    def test_stats_empty_period(self, tmp_db: IrrigationRepository) -> None:
        """Stats with no events in period returns zero counts."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        tmp_db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name=FAKE_IRRIGATOR_NAME,
            irrigator_type="rainpoint.ik10pw",
            config={},
        )

        stats = get_irrigation_stats(tmp_db, cluster_id, days=1)
        assert "error" not in stats
        assert stats["total_events"] == 0
        assert stats["total_duration_minutes"] == 0


class TestStatsWithoutEvents:
    """``get_irrigation_stats`` when the irrigator has no events."""

    def test_stats_no_events_returns_zero_frequency(self, tmp_db: IrrigationRepository) -> None:
        """Stats with irrigator but no events returns zero frequency."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        tmp_db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name=FAKE_IRRIGATOR_NAME,
            irrigator_type="rainpoint.ik10pw",
            config={},
        )

        stats = get_irrigation_stats(tmp_db, cluster_id, days=7)
        assert "error" not in stats
        assert stats["frequency_per_day"] == 0
        assert stats["avg_duration_minutes"] == 0
