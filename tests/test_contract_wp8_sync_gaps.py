"""Gap tests: ``greenhouse_core.sync`` behavior pinned before the sync loop was restructured.

Characterization (current behavior), green before and after that restructuring. The existing
``test_contract_sync.py`` golden sorts dict keys and never logs a sensor with both
new log rows and a live save, nor exactly one new row (mutation survivor
``new > 0 -> new > 1`` in ``sync_sensor_data``). These pin:

- the per-sensor summary line for every combination of new rows / live save;
- the insertion order of the stats dict keys (the server's ``SyncResult`` and the
  JSON the API returns are built from it);
- that one failing sensor neither stops the loop nor contributes to the totals
  (sensors iterate by name within a cluster).
"""

from __future__ import annotations

import logging

import pytest

from golden import FROZEN_TS
from greenhouse_core.devices.gateway import group_logs_by_timestamp
from greenhouse_core.sync import sync_sensor_data

STATS_KEYS = ["total_synced", "total_new", "total_live", "errors"]


class _Gateway:
    """Minimal recording gateway: canned logs / live readings per device id."""

    def __init__(self, *, logs: dict | None = None, live: dict | None = None) -> None:
        self.logs = logs or {}
        self.live = live or {}
        self.calls: list[tuple[str, str]] = []

    def get_device_logs(self, device_id, since_ms=None, hours=24, max_records=100):
        self.calls.append(("get_device_logs", device_id))
        value = self.logs.get(device_id, [])
        if isinstance(value, BaseException):
            raise value
        return value

    def group_logs_by_timestamp(self, logs, tolerance_ms=5000):
        return group_logs_by_timestamp(logs, tolerance_ms)

    def get_live_reading(self, device_id):
        self.calls.append(("get_live_reading", device_id))
        return self.live.get(device_id, {})


def _log(ts: int, key: str, value) -> dict:
    return {"timestamp_ms": ts * 1000, "timestamp": ts, "code": key, "raw_value": value, "key": key, "value": value}


def _sync_lines(caplog) -> list[tuple[str, str]]:
    return [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "greenhouse_core.sync"]


@pytest.mark.parametrize(
    ("n_logs", "live", "summary"),
    [
        (1, {}, "1 new from logs"),
        (1, {"soil_moisture": 30.0}, "1 new from logs, live ✓"),
        (2, {"soil_moisture": 30.0}, "2 new from logs, live ✓"),
        (0, {"soil_moisture": 30.0}, "live ✓"),
        (0, {}, "up to date"),
    ],
)
def test_per_sensor_summary_line(tmp_db, frozen_clock, caplog, n_logs, live, summary):
    cluster_id = tmp_db.add_cluster("Bench")
    tmp_db.add_sensor(
        cluster_id=cluster_id, tuya_device_id="dev_p", name="Probe", sensor_type="soil_moisture", config={}
    )
    tmp_db.session.commit()
    logs = [_log(FROZEN_TS - 600 * (i + 1), "soil_moisture", 40.0) for i in range(n_logs)]
    gw = _Gateway(logs={"dev_p": logs}, live={"dev_p": live})

    with caplog.at_level(logging.INFO, logger="greenhouse_core.sync"):
        stats = sync_sensor_data(tmp_db, gw)

    assert _sync_lines(caplog) == [("INFO", "[Bench] Syncing 1 sensor(s)..."), ("INFO", f"  Probe: {summary}")]
    assert stats == {"total_synced": n_logs, "total_new": n_logs, "total_live": 1 if live else 0, "errors": []}


def test_stats_key_order_without_clusters(tmp_db):
    assert list(sync_sensor_data(tmp_db, _Gateway())) == STATS_KEYS


def test_stats_key_order_and_failure_isolation(tmp_db, frozen_clock, caplog):
    cluster_id = tmp_db.add_cluster("Bench")
    for name, dev in (("Alpha", "dev_1"), ("Broken", "dev_x"), ("Charlie", "dev_2")):
        tmp_db.add_sensor(cluster_id=cluster_id, tuya_device_id=dev, name=name, sensor_type="soil_moisture", config={})
    tmp_db.session.commit()
    gw = _Gateway(
        logs={
            "dev_1": [_log(FROZEN_TS - 600, "soil_moisture", 40.0)],
            "dev_x": KeyError("result"),
            "dev_2": [_log(FROZEN_TS - 600, "temperature", 20.0)],
        },
        live={"dev_2": {"temperature": 21.0}},
    )

    with caplog.at_level(logging.INFO, logger="greenhouse_core.sync"):
        stats = sync_sensor_data(tmp_db, gw)

    assert list(stats) == STATS_KEYS
    assert stats == {"total_synced": 2, "total_new": 2, "total_live": 1, "errors": ["Broken: 'result'"]}
    assert [c for c in gw.calls if c[1] == "dev_x"] == [("get_device_logs", "dev_x")]
    assert _sync_lines(caplog)[1:] == [
        ("INFO", "  Alpha: 1 new from logs"),
        ("ERROR", "  Broken: 'result'"),
        ("INFO", "  Charlie: 1 new from logs, live ✓"),
    ]
