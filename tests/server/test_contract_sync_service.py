"""Characterization: ``SyncService.ensure_fresh_and_read`` freshness gate (invariant #8, gap I8a).

The irrigation pipeline reads the persisted snapshot from SQLite. Only when a
sensor's newest row is missing or older than ``SENSOR_READING_STALE_SECONDS`` (4 h)
does it force **one** targeted ``sync_single_sensor`` — for that sensor only, with
``hours=6`` — then re-read. A fresh reading costs zero gateway calls. The boundary
is strict (``age > threshold`` is stale; ``age == threshold`` is fresh).

All timestamps derive from ``FROZEN_INSTANT``; the gateway is a recording fake.
"""

from __future__ import annotations

import logging

import pytest

from golden import FROZEN_TS
from greenhouse_core.constants import SENSOR_READING_STALE_SECONDS
from greenhouse_server.services import sync as sync_service_module
from greenhouse_server.services.sync import SNAPSHOT_LOOKBACK_HOURS, SyncService


class RecordingGateway:
    """Fake DeviceGateway recording every call ``sync_single_sensor`` makes."""

    def __init__(self, *, live: dict | None = None, logs_error: Exception | None = None) -> None:
        self.live = live or {}
        self.logs_error = logs_error
        self.calls: list[tuple] = []

    def get_device_logs(self, device_id, since_ms=None, hours=24, max_records=100):
        self.calls.append(("get_device_logs", device_id, since_ms))
        if self.logs_error is not None:
            raise self.logs_error
        return []

    def group_logs_by_timestamp(self, logs, tolerance_ms=5000):
        self.calls.append(("group_logs_by_timestamp", len(logs)))
        return []

    def get_live_reading(self, device_id):
        self.calls.append(("get_live_reading", device_id))
        return dict(self.live.get(device_id, {}))


@pytest.fixture
def cluster(tmp_db, frozen_clock):
    """Two sensors in one cluster (names sort Alpha < Beta), plus an unrelated cluster's sensor."""
    cid = tmp_db.add_cluster("Fresh Cluster")
    other = tmp_db.add_cluster("Other Cluster")
    alpha = tmp_db.add_sensor(
        cluster_id=cid, tuya_device_id="dev_alpha", name="Alpha", sensor_type="tuya.tr301z", config={}
    )
    beta = tmp_db.add_sensor(
        cluster_id=cid, tuya_device_id="dev_beta", name="Beta", sensor_type="tuya.tr301z", config={}
    )
    tmp_db.add_sensor(cluster_id=other, tuya_device_id="dev_other", name="Other", sensor_type="tuya.tr301z", config={})
    tmp_db.session.commit()
    return tmp_db, cid, alpha, beta


@pytest.fixture
def spy_sync(monkeypatch):
    """Record (sensor_id, hours) for every targeted sync, delegating to the real function."""
    calls: list[tuple[int, int]] = []
    real = sync_service_module.sync_single_sensor

    def _spy(db, cloud, sensor, hours):
        calls.append((sensor.id, hours))
        return real(db, cloud, sensor, hours)

    monkeypatch.setattr(sync_service_module, "sync_single_sensor", _spy)
    return calls


def _seed(db, sensor_id: int, age: int, **values) -> None:
    db.add_sensor_reading(sensor_id=sensor_id, timestamp=FROZEN_TS - age, **values)


def test_threshold_constant_is_four_hours():
    assert SENSOR_READING_STALE_SECONDS == 14400
    assert SNAPSHOT_LOOKBACK_HOURS == 24


def test_fresh_readings_cost_zero_gateway_calls(cluster, spy_sync):
    db, cid, alpha, beta = cluster
    _seed(db, alpha, 60, soil_moisture=40.0, temperature=20.0)
    _seed(db, beta, SENSOR_READING_STALE_SECONDS - 1, soil_moisture=35.0, temperature=24.0, light=800)
    gw = RecordingGateway()
    snapshot = SyncService(db, None, gw).ensure_fresh_and_read(cid)
    assert gw.calls == []
    assert spy_sync == []
    assert snapshot == {"temperature": 22.0, "soil_moisture": 35.0, "env_humidity": None, "light": 800}


@pytest.mark.parametrize(
    ("age", "stale"),
    [
        (SENSOR_READING_STALE_SECONDS - 1, False),
        (SENSOR_READING_STALE_SECONDS, False),  # exactly at the threshold → still fresh (strict >)
        (SENSOR_READING_STALE_SECONDS + 1, True),
    ],
)
def test_boundary_at_exactly_the_threshold(cluster, spy_sync, age, stale):
    db, cid, alpha, beta = cluster
    _seed(db, alpha, age, soil_moisture=40.0)
    _seed(db, beta, 10, soil_moisture=50.0)
    gw = RecordingGateway()
    SyncService(db, None, gw).ensure_fresh_and_read(cid)
    assert spy_sync == ([(alpha, 6)] if stale else [])
    assert len(gw.calls) == (3 if stale else 0)


def test_stale_sensor_gets_exactly_one_targeted_sync(cluster, spy_sync):
    db, cid, alpha, beta = cluster
    stale_ts = FROZEN_TS - SENSOR_READING_STALE_SECONDS - 600
    _seed(db, alpha, SENSOR_READING_STALE_SECONDS + 600, soil_moisture=40.0)
    _seed(db, beta, 30, soil_moisture=50.0)
    gw = RecordingGateway(live={"dev_alpha": {"soil_moisture": 31.0, "temperature": 21.0}})
    snapshot = SyncService(db, None, gw).ensure_fresh_and_read(cid)
    assert spy_sync == [(alpha, 6)]
    assert gw.calls == [
        ("get_device_logs", "dev_alpha", (stale_ts - 60) * 1000),
        ("group_logs_by_timestamp", 0),
        ("get_live_reading", "dev_alpha"),
    ]
    # The freshly synced live row (persisted at now) is visible to the snapshot.
    assert snapshot == {"temperature": 21.0, "soil_moisture": 31.0, "env_humidity": None, "light": None}


def test_sensor_without_any_reading_is_synced_with_a_six_hour_window(cluster, spy_sync):
    db, cid, alpha, beta = cluster
    _seed(db, beta, 30, soil_moisture=50.0)
    gw = RecordingGateway(live={"dev_alpha": {"soil_moisture": 44.0}})
    snapshot = SyncService(db, None, gw).ensure_fresh_and_read(cid)
    assert spy_sync == [(alpha, 6)]
    assert gw.calls[0] == ("get_device_logs", "dev_alpha", (FROZEN_TS - 6 * 3600) * 1000)
    assert snapshot["soil_moisture"] == 44.0


def test_all_stale_sync_in_sensor_name_order(cluster, spy_sync):
    db, cid, alpha, beta = cluster
    gw = RecordingGateway()
    assert SyncService(db, None, gw).ensure_fresh_and_read(cid) is None
    assert spy_sync == [(alpha, 6), (beta, 6)]
    assert [c[1] for c in gw.calls if c[0] == "get_device_logs"] == ["dev_alpha", "dev_beta"]


def test_no_reading_at_all_and_no_cloud_returns_none_without_sync(cluster, spy_sync):
    db, cid, _, _ = cluster
    assert SyncService(db, None, None).ensure_fresh_and_read(cid) is None
    assert spy_sync == []


def test_stale_but_no_cloud_reads_what_is_archived(cluster, spy_sync):
    db, cid, alpha, _ = cluster
    _seed(db, alpha, SENSOR_READING_STALE_SECONDS + 1, soil_moisture=40.0, env_humidity=55.0)
    assert SyncService(db, None, None).ensure_fresh_and_read(cid) == {
        "temperature": None,
        "soil_moisture": 40.0,
        "env_humidity": 55.0,
        "light": None,
    }
    assert spy_sync == []


def test_archived_rows_older_than_the_lookback_are_ignored_by_the_snapshot(cluster):
    db, cid, alpha, _ = cluster
    _seed(db, alpha, SNAPSHOT_LOOKBACK_HOURS * 3600 + 1, soil_moisture=40.0)
    assert SyncService(db, None, None).ensure_fresh_and_read(cid) is None


def test_failed_freshness_sync_is_swallowed_and_logged_at_debug(cluster, spy_sync, caplog):
    db, cid, alpha, beta = cluster
    _seed(db, beta, 30, soil_moisture=50.0)
    gw = RecordingGateway(logs_error=RuntimeError("token expired"))
    with caplog.at_level(logging.DEBUG, logger="greenhouse_server.services.sync"):
        snapshot = SyncService(db, None, gw).ensure_fresh_and_read(cid)
    assert snapshot == {"temperature": None, "soil_moisture": 50.0, "env_humidity": None, "light": None}
    assert spy_sync == [(alpha, 6)]
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("DEBUG", "Freshness sync failed for sensor Alpha")
    ]


def test_empty_cluster_returns_none_with_zero_calls(tmp_db, spy_sync):
    cid = tmp_db.add_cluster("Empty")
    gw = RecordingGateway()
    assert SyncService(tmp_db, None, gw).ensure_fresh_and_read(cid) is None
    assert gw.calls == [] and spy_sync == []


class TestSyncAllSensors:
    def test_without_cloud_returns_the_fixed_error_payload(self, tmp_db):
        assert SyncService(tmp_db, None, None).sync_all_sensors() == {
            "total_synced": 0,
            "total_new": 0,
            "total_live": 0,
            "errors": ["No cloud connection"],
        }

    def test_with_cloud_delegates_to_core_sync_with_hours(self, tmp_db, monkeypatch):
        seen = []
        monkeypatch.setattr(
            sync_service_module, "core_sync", lambda repo, cloud, hours: seen.append((repo, cloud, hours)) or {"ok": 1}
        )
        gw = RecordingGateway()
        assert SyncService(tmp_db, None, gw).sync_all_sensors(hours=3) == {"ok": 1}
        assert seen == [(tmp_db, gw, 3)]
