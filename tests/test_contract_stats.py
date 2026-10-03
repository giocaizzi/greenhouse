"""Characterization tests for ``greenhouse_core.stats`` gaps left by the Phase-1 net.

Pins ``get_irrigation_stats``'s defensive cutoff filter and ``print_stats_report`` (no
production caller today — kept and pinned as is; see REFACTOR_NOTES.md dead-code list).
"""

import pytest

import greenhouse_core.repository as repo_mod
import greenhouse_core.utils as utils_mod
from fake_data import FAKE_CLUSTER_NAME, FAKE_DEVICE_ID, FAKE_IRRIGATOR_NAME
from golden import FROZEN_TS
from greenhouse_core.stats import get_irrigation_stats, print_stats_report

DAY = 24 * 3600


@pytest.fixture
def utc_display(monkeypatch):
    """Display timezone pinned to UTC regardless of process state or ``IRRIGATION_TZ``."""
    monkeypatch.setattr(utils_mod, "_display_timezone", None)
    monkeypatch.delenv("IRRIGATION_TZ", raising=False)


def _cluster_with_irrigator(db):
    cluster_id = db.add_cluster(FAKE_CLUSTER_NAME)
    irrigator_id = db.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id=FAKE_DEVICE_ID,
        name=FAKE_IRRIGATOR_NAME,
        irrigator_type="tuya_cloud",
        config={},
    )
    return cluster_id, irrigator_id


def _rewind_repository_clock(monkeypatch, seconds: int) -> None:
    """Make the repository's own ``time.time()`` read earlier than the caller's.

    ``stats`` and ``get_recent_events`` each compute a cutoff from ``time.time()``;
    in production the repository reads second, so its window is never wider. Rewinding
    only the repository clock is the one way to reach the defensive ``< cutoff`` skip.
    """

    class _RewoundTime:
        @staticmethod
        def time() -> float:
            return FROZEN_TS - seconds

    monkeypatch.setattr(repo_mod, "time", _RewoundTime)


def test_get_irrigation_stats_skips_events_older_than_its_own_cutoff(tmp_db, frozen_clock, monkeypatch):
    cluster_id, irrigator_id = _cluster_with_irrigator(tmp_db)
    tmp_db.add_irrigation_event(irrigator_id, "start", "auto", duration_minutes=4, timestamp=FROZEN_TS - 600)
    tmp_db.add_irrigation_event(irrigator_id, "start", "auto", duration_minutes=9, timestamp=FROZEN_TS - DAY - 60)
    tmp_db.session.commit()
    _rewind_repository_clock(monkeypatch, seconds=2 * 3600)

    stats = get_irrigation_stats(tmp_db, cluster_id, days=1)

    assert stats["total_events"] == 1
    assert stats["total_duration_minutes"] == 4
    assert [irr["timestamp"] for irr in stats["irrigations"]] == [FROZEN_TS - 600]


def test_print_stats_report_error(capsys):
    print_stats_report({"error": "No irrigators in cluster"}, "Balcony")

    assert capsys.readouterr().out == "❌ No irrigators in cluster\n"


def test_print_stats_report_empty_period(capsys):
    stats = {
        "period_days": 7,
        "total_events": 0,
        "total_duration_minutes": 0,
        "events_by_type": {},
        "events_by_trigger": {},
        "irrigations": [],
        "avg_duration_minutes": 0,
        "frequency_per_day": 0,
    }

    print_stats_report(stats, "Balcony")

    assert capsys.readouterr().out == (
        "\n📊 Irrigation Statistics - Balcony\n"
        "   Period: last 7 days\n"
        "\n🔢 Summary:\n"
        "   Total events: 0\n"
        "   Irrigations: 0\n"
        "   Total water time: 0min\n"
    )


def test_print_stats_report_full(capsys, utc_display):
    irrigations = [
        {"timestamp": FROZEN_TS - n * 3600, "duration_minutes": 10 + n, "triggered_by": "auto", "irrigator": "Pump"}
        for n in range(6, 0, -1)
    ]
    stats = {
        "period_days": 2,
        "total_events": 8,
        "total_duration_minutes": 81,
        "events_by_type": {"stop": 2, "start": 6},
        "events_by_trigger": {"manual": 1, "auto": 7},
        "irrigations": irrigations,
        "avg_duration_minutes": 13.5,
        "frequency_per_day": 3.0,
    }

    print_stats_report(stats, "Balcony")

    out = capsys.readouterr().out
    assert out == (
        "\n📊 Irrigation Statistics - Balcony\n"
        "   Period: last 2 days\n"
        "\n🔢 Summary:\n"
        "   Total events: 8\n"
        "   Irrigations: 6\n"
        "   Total water time: 1h 21min\n"
        "   Average per irrigation: 13min\n"
        "   Frequency: 3.0 times/day\n"
        "\n📋 Events by type:\n"
        "   start: 6\n"
        "   stop: 2\n"
        "\n🎯 Triggered by:\n"
        "   auto: 7\n"
        "   manual: 1\n"
        "\n💧 Recent irrigations:\n"
        "   2026-04-15 05:00 | 15min | auto | Pump\n"
        "   2026-04-15 06:00 | 14min | auto | Pump\n"
        "   2026-04-15 07:00 | 13min | auto | Pump\n"
        "   2026-04-15 08:00 | 12min | auto | Pump\n"
        "   2026-04-15 09:00 | 11min | auto | Pump\n"
    )
