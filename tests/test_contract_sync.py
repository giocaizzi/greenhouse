"""Characterization: ``greenhouse_core.sync`` — the sole Cloud reader of sensor data.

Invariant #8 ("one Cloud writer, everyone else reads SQLite"): the sync job is the
only code that pulls sensor data from Tuya. Per sensor it backfills via
``get_device_logs`` (``getdevicelog``), folds the entries with
``group_logs_by_timestamp``, then does exactly **one** live read; every row is
de-duplicated on ``(sensor_id, timestamp)``. These tests pin the exact gateway
calls (method + args), the rows persisted, the returned counters and stats shape,
the log lines, and every error path — against a recording fake gateway, with the
clock frozen at ``FROZEN_INSTANT``.
"""

from __future__ import annotations

import logging

import pytest

from golden import FROZEN_TS, assert_golden_json
from greenhouse_core.devices.gateway import group_logs_by_timestamp
from greenhouse_core.sync import sync_sensor_data, sync_single_sensor

GOLDEN = "ingress_devices"


class FakeGateway:
    """Records every gateway call ``greenhouse_core.sync`` makes; replays canned data.

    ``logs`` / ``live`` are keyed by Tuya device id. A value that is an exception
    instance is raised. ``group_logs_by_timestamp`` delegates to the real folding
    function so the test exercises the real grouping contract.
    """

    def __init__(self, *, logs: dict | None = None, live: dict | None = None) -> None:
        """Serve canned device logs and live readings per device id (an exception value is raised)."""
        self.logs = logs or {}
        self.live = live or {}
        self.calls: list[tuple] = []

    def get_device_logs(self, device_id, since_ms=None, hours=24, max_records=100):
        """Record the log request and return (or raise) the canned logs."""
        self.calls.append(
            ("get_device_logs", device_id, {"since_ms": since_ms, "hours": hours, "max_records": max_records})
        )
        value = self.logs.get(device_id, [])
        if isinstance(value, BaseException):
            raise value
        return value

    def group_logs_by_timestamp(self, logs, tolerance_ms=5000):
        """Record the call and group with the real implementation."""
        self.calls.append(("group_logs_by_timestamp", len(logs)))
        return group_logs_by_timestamp(logs, tolerance_ms)

    def get_live_reading(self, device_id):
        """Record the live read and return (or raise) the canned reading."""
        self.calls.append(("get_live_reading", device_id))
        value = self.live.get(device_id, {})
        if isinstance(value, BaseException):
            raise value
        return value


def _log(ts: int, key: str, value, *, offset_ms: int = 0) -> dict:
    """A parsed ``get_device_logs`` entry (the shape the real gateway returns)."""
    return {
        "timestamp_ms": ts * 1000 + offset_ms,
        "timestamp": ts,
        "code": key,
        "raw_value": value,
        "key": key,
        "value": value,
    }


def _rows(db, sensor_id: int) -> list[dict]:
    from greenhouse_core.models import SensorReading

    rows = (
        db.session.query(SensorReading)
        .filter(SensorReading.sensor_id == sensor_id)
        .order_by(SensorReading.timestamp)
        .all()
    )
    return [
        {
            "timestamp": r.timestamp,
            "temperature": r.temperature,
            "soil_moisture": r.soil_moisture,
            "light": r.light,
            "env_humidity": r.env_humidity,
            "battery_state": r.battery_state,
            "water_warning": r.water_warning,
        }
        for r in rows
    ]


def _add_sensor(db, cluster_id: int, name: str, device_id: str) -> int:
    return db.add_sensor(
        cluster_id=cluster_id, tuya_device_id=device_id, name=name, sensor_type="tuya.tr301z", config={}
    )


@pytest.fixture
def one_sensor(tmp_db, frozen_clock):
    """Provide a database with one cluster and one sensor, clock frozen."""
    cluster_id = tmp_db.add_cluster("Sync Cluster")
    sensor_id = _add_sensor(tmp_db, cluster_id, "Probe A", "dev_a")
    tmp_db.session.commit()
    return tmp_db, tmp_db.get_sensor(sensor_id)


# ── sync_single_sensor ────────────────────────────────────────────────────────


class TestSyncWindow:
    """Which time window a sensor sync asks the Cloud for."""

    @pytest.mark.parametrize("hours", [24, 6, 1])
    def test_first_sync_pulls_the_hours_window_from_now(self, one_sensor, hours):
        """A sensor's first sync pulls ``hours`` back from now."""
        db, sensor = one_sensor
        gw = FakeGateway()
        sync_single_sensor(db, gw, sensor, hours)
        assert gw.calls == [
            (
                "get_device_logs",
                "dev_a",
                {"since_ms": (FROZEN_TS - hours * 3600) * 1000, "hours": 24, "max_records": 100},
            ),
            ("group_logs_by_timestamp", 0),
            ("get_live_reading", "dev_a"),
        ]

    def test_later_sync_starts_one_minute_before_the_last_row(self, one_sensor):
        """Later syncs resume one minute before the newest stored row."""
        db, sensor = one_sensor
        db.add_sensor_reading(sensor_id=sensor.id, timestamp=FROZEN_TS - 7200, soil_moisture=40.0)
        db.add_sensor_reading(sensor_id=sensor.id, timestamp=FROZEN_TS - 3600, soil_moisture=41.0)
        gw = FakeGateway()
        sync_single_sensor(db, gw, sensor, 24)
        assert gw.calls[0] == (
            "get_device_logs",
            "dev_a",
            {"since_ms": (FROZEN_TS - 3600 - 60) * 1000, "hours": 24, "max_records": 100},
        )


class TestBackfillAndDedup:
    """Log backfill: grouping, de-duplication and counters."""

    def test_grouped_logs_persist_deduplicated_and_counted(self, one_sensor):
        """Grouped logs are stored once each, skipping rows already archived, and counted."""
        db, sensor = one_sensor
        t1, t2, t3 = FROZEN_TS - 3000, FROZEN_TS - 2000, FROZEN_TS - 1000
        db.add_sensor_reading(sensor_id=sensor.id, timestamp=t2, soil_moisture=11.0)  # already archived
        gw = FakeGateway(
            logs={
                "dev_a": [
                    _log(t1, "temperature", 21.5),
                    _log(t1, "soil_moisture", 44.0, offset_ms=2000),
                    _log(t2, "soil_moisture", 45.0),  # duplicate (sensor_id, timestamp) → skipped
                    _log(t3, "light", 900),
                    _log(t3, "env_humidity", 61.0, offset_ms=100),
                    _log(t3, "battery_state", "middle", offset_ms=200),
                    _log(t3, "water_warning", True, offset_ms=300),  # persisted from logs
                ]
            }
        )
        assert sync_single_sensor(db, gw, sensor, 24) == (3, 2, 0)
        assert _rows(db, sensor.id) == [
            {
                "timestamp": t1,
                "temperature": 21.5,
                "soil_moisture": 44.0,
                "light": None,
                "env_humidity": None,
                "battery_state": None,
                "water_warning": None,
            },
            {
                "timestamp": t2,
                "temperature": None,
                "soil_moisture": 11.0,  # the archived row wins; the log value is dropped
                "light": None,
                "env_humidity": None,
                "battery_state": None,
                "water_warning": None,
            },
            {
                "timestamp": t3,
                "temperature": None,
                "soil_moisture": None,
                "light": 900,
                "env_humidity": 61.0,
                "battery_state": "middle",
                "water_warning": True,
            },
        ]

    @pytest.mark.parametrize(("logged", "stored"), [(False, False), (True, True), ("abc", None), ("false", None)])
    def test_log_water_warning_is_stored_only_as_a_parsed_bool(self, one_sensor, logged, stored):
        """A parsed boolean is stored; a raw string the DP parser rejected is stored as unknown."""
        db, sensor = one_sensor
        ts = FROZEN_TS - 600
        gw = FakeGateway(logs={"dev_a": [_log(ts, "soil_moisture", 40.0), _log(ts, "water_warning", logged)]})
        assert sync_single_sensor(db, gw, sensor, 24) == (1, 1, 0)
        assert [r["water_warning"] for r in _rows(db, sensor.id)] == [stored]

    def test_grouped_reading_without_timestamp_is_skipped_but_counted(self, one_sensor):
        """A grouped reading without a timestamp is counted but not stored."""
        db, sensor = one_sensor
        gw = FakeGateway(logs={"dev_a": [_log(0, "soil_moisture", 30.0), _log(FROZEN_TS - 60, "soil_moisture", 31.0)]})
        assert sync_single_sensor(db, gw, sensor, 24) == (2, 1, 0)
        assert [r["timestamp"] for r in _rows(db, sensor.id)] == [FROZEN_TS - 60]

    def test_rerunning_the_same_window_inserts_nothing_new(self, one_sensor):
        """Re-syncing the same window stores nothing new."""
        db, sensor = one_sensor
        logs = [_log(FROZEN_TS - 600, "soil_moisture", 50.0)]
        gw = FakeGateway(logs={"dev_a": logs}, live={"dev_a": {"soil_moisture": 49.0}})
        assert sync_single_sensor(db, gw, sensor, 24) == (1, 1, 1)
        assert sync_single_sensor(db, gw, sensor, 24) == (1, 0, 0)
        assert len(_rows(db, sensor.id)) == 2

    def test_log_fetch_error_propagates_before_live_read(self, one_sensor):
        """A log fetch error propagates before the live read is attempted."""
        db, sensor = one_sensor
        gw = FakeGateway(logs={"dev_a": RuntimeError("Cloud API error: token invalid")})
        with pytest.raises(RuntimeError, match="token invalid"):
            sync_single_sensor(db, gw, sensor, 24)
        assert [c[0] for c in gw.calls] == ["get_device_logs"]


class TestLiveRead:
    """The one live read per sensor that follows the backfill."""

    def test_live_reading_is_persisted_at_now_with_water_warning(self, one_sensor):
        """The live reading is stored at now, including the water warning."""
        db, sensor = one_sensor
        live = {
            "temperature": 22.0,
            "soil_moisture": 47.0,
            "light": 1500,
            "env_humidity": 58.0,
            "battery_state": "high",
            "water_warning": False,
            "soil_warning": 0,
        }
        gw = FakeGateway(live={"dev_a": live})
        assert sync_single_sensor(db, gw, sensor, 24) == (0, 0, 1)
        assert _rows(db, sensor.id) == [
            {
                "timestamp": FROZEN_TS,
                "temperature": 22.0,
                "soil_moisture": 47.0,
                "light": 1500,
                "env_humidity": 58.0,
                "battery_state": "high",
                "water_warning": False,
            }
        ]

    @pytest.mark.parametrize(
        ("live", "saved"),
        [
            ({"temperature": 20.0}, 1),
            ({"soil_moisture": 33.0}, 1),
            ({"light": 10}, 1),
            ({"env_humidity": 64.0}, 1),
            ({"humidity": 70.0}, 0),  # not a canonical key (the gateway maps DP "humidity" to soil_moisture)
            ({}, 0),
            ({"battery_state": "low", "water_warning": True}, 0),
            ({"error": "device offline"}, 0),
        ],
    )
    def test_live_persistence_guard(self, one_sensor, live, saved):
        """A live reading is stored only when it carries a persisted field."""
        db, sensor = one_sensor
        assert sync_single_sensor(db, FakeGateway(live={"dev_a": live}), sensor, 24) == (0, 0, saved)
        assert len(_rows(db, sensor.id)) == saved

    def test_env_humidity_only_live_reading_is_persisted(self, one_sensor):
        """B-21 fixed: the guard checks the canonical keys, so an ``env_humidity``-only live read is stored."""
        db, sensor = one_sensor
        assert sync_single_sensor(db, FakeGateway(live={"dev_a": {"env_humidity": 64.0}}), sensor, 24) == (0, 0, 1)
        assert _rows(db, sensor.id) == [
            {
                "timestamp": FROZEN_TS,
                "temperature": None,
                "soil_moisture": None,
                "light": None,
                "env_humidity": 64.0,
                "battery_state": None,
                "water_warning": None,
            }
        ]

    def test_live_error_is_logged_and_not_raised(self, one_sensor, caplog):
        """A live-read error is logged and the backfilled rows are kept."""
        db, sensor = one_sensor
        gw = FakeGateway(
            logs={"dev_a": [_log(FROZEN_TS - 60, "soil_moisture", 31.0)]},
            live={"dev_a": RuntimeError("Cloud API error")},
        )
        with caplog.at_level(logging.WARNING, logger="greenhouse_core.sync"):
            assert sync_single_sensor(db, gw, sensor, 24) == (1, 1, 0)
        assert [(r.levelname, r.getMessage(), r.exc_info is not None) for r in caplog.records] == [
            ("WARNING", "  Probe A: live read failed", True)
        ]

    def test_live_row_colliding_with_a_log_row_at_now_is_not_counted(self, one_sensor):
        """A live row colliding with a log row at the same instant is neither stored nor counted."""
        db, sensor = one_sensor
        gw = FakeGateway(
            logs={"dev_a": [_log(FROZEN_TS, "soil_moisture", 31.0)]},
            live={"dev_a": {"soil_moisture": 99.0}},
        )
        assert sync_single_sensor(db, gw, sensor, 24) == (1, 1, 0)
        assert [r["soil_moisture"] for r in _rows(db, sensor.id)] == [31.0]


# ── sync_sensor_data (all clusters) ───────────────────────────────────────────


def test_sync_all_clusters_scenario_golden(tmp_db, frozen_clock, caplog):
    """Cluster/sensor iteration order, per-sensor calls, stats, persisted rows and log lines."""
    db = tmp_db
    zeta = db.add_cluster("Zeta Balcony")
    alpha = db.add_cluster("Alpha Kitchen")
    db.add_cluster("Empty Shelf")  # no sensors → skipped silently
    s_fail = _add_sensor(db, zeta, "Broken Probe", "dev_broken")
    s_new = _add_sensor(db, alpha, "Basil Probe", "dev_basil")
    s_live = _add_sensor(db, alpha, "Aloe Probe", "dev_aloe")
    s_idle = _add_sensor(db, zeta, "Idle Probe", "dev_idle")
    db.add_sensor_reading(sensor_id=s_idle, timestamp=FROZEN_TS - 900, soil_moisture=40.0)
    db.session.commit()

    gw = FakeGateway(
        logs={
            "dev_basil": [
                _log(FROZEN_TS - 1800, "soil_moisture", 38.0),
                _log(FROZEN_TS - 1800, "temperature", 19.5, offset_ms=900),
                _log(FROZEN_TS - 900, "soil_moisture", 37.0),
            ],
            "dev_broken": ValueError("bad payload"),
            "dev_idle": [_log(FROZEN_TS - 900, "soil_moisture", 40.0)],  # already archived
        },
        live={"dev_aloe": {"soil_moisture": 25.0, "temperature": 23.0}, "dev_idle": {}},
    )
    with caplog.at_level(logging.INFO, logger="greenhouse_core.sync"):
        stats = sync_sensor_data(db, gw, hours=12)

    assert stats == {
        "total_synced": 3,
        "total_new": 2,
        "total_live": 1,
        "errors": ["Broken Probe: bad payload"],
    }
    assert_golden_json(
        f"{GOLDEN}/core_sync_all_clusters.json",
        {
            "stats": stats,
            "gateway_calls": gw.calls,
            "rows": {
                "Aloe Probe": _rows(db, s_live),
                "Basil Probe": _rows(db, s_new),
                "Broken Probe": _rows(db, s_fail),
                "Idle Probe": _rows(db, s_idle),
            },
            "log": [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "greenhouse_core.sync"],
        },
    )


def test_sync_with_no_clusters_returns_zeroed_stats(tmp_db):
    """With no clusters the sync returns zeroed stats and makes no calls."""
    gw = FakeGateway()
    assert sync_sensor_data(tmp_db, gw) == {"total_synced": 0, "total_new": 0, "total_live": 0, "errors": []}
    assert gw.calls == []


def test_sync_default_hours_is_24_for_first_sync(tmp_db, frozen_clock):
    """A first sync without an explicit window pulls 24 hours."""
    cluster_id = tmp_db.add_cluster("C")
    _add_sensor(tmp_db, cluster_id, "P", "dev_p")
    tmp_db.session.commit()
    gw = FakeGateway()
    sync_sensor_data(tmp_db, gw)
    assert gw.calls[0][2]["since_ms"] == (FROZEN_TS - 24 * 3600) * 1000


def test_sync_does_not_commit(tmp_db, frozen_clock):
    """Persistence is left to the caller's transaction: a rollback discards the sync."""
    cluster_id = tmp_db.add_cluster("C")
    sensor_id = _add_sensor(tmp_db, cluster_id, "P", "dev_p")
    tmp_db.session.commit()
    sync_sensor_data(tmp_db, FakeGateway(live={"dev_p": {"soil_moisture": 10.0}}))
    assert len(_rows(tmp_db, sensor_id)) == 1
    tmp_db.session.rollback()
    assert _rows(tmp_db, sensor_id) == []
