"""Characterization tests for ``greenhouse_core.stats`` gaps left by the Phase-1 net.

Pins ``get_irrigation_stats``'s defensive cutoff filter.
"""

import pytest

import greenhouse_core.repository as repo_mod
import greenhouse_core.utils as utils_mod
from fake_data import FAKE_CLUSTER_NAME, FAKE_DEVICE_ID, FAKE_IRRIGATOR_NAME
from golden import FROZEN_TS
from greenhouse_core.stats import get_irrigation_stats

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
