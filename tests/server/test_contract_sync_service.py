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
from datetime import timedelta
from typing import TYPE_CHECKING, Any, TypedDict, Unpack, cast

import pytest

from golden import FROZEN_TS
from greenhouse_core.constants import SENSOR_READING_STALE_SECONDS, SNAPSHOT_LOOKBACK_HOURS
from greenhouse_core.sync import sync_single_sensor
from greenhouse_server.services import sync as sync_service_module
from greenhouse_server.services.sync import SyncService

if TYPE_CHECKING:
    from time_machine import Traveller

    from greenhouse_core.devices import DeviceGateway
    from greenhouse_core.models import Sensor
    from greenhouse_core.repository import IrrigationRepository
    from greenhouse_core.sync import SyncStats


class RecordingGateway:
    """Fake DeviceGateway recording every call ``sync_single_sensor`` makes."""

    def __init__(self, *, live: dict[str, dict[str, float]] | None = None, logs_error: Exception | None = None) -> None:
        """Serve per-device ``live`` readings, optionally fail log fetches, and record every call."""
        self.live = live or {}
        self.logs_error = logs_error
        self.calls: list[tuple[object, ...]] = []

    def get_device_logs(
        self, device_id: str, since_ms: int | None = None, _hours: int = 24, _max_records: int = 100
    ) -> list[dict[str, Any]]:
        """Record the call; raise the configured error or return no logs."""
        self.calls.append(("get_device_logs", device_id, since_ms))
        if self.logs_error is not None:
            raise self.logs_error
        return []

    def group_logs_by_timestamp(self, logs: list[dict[str, Any]], _tolerance_ms: int = 5000) -> list[dict[str, Any]]:
        """Record how many logs were grouped; there are no groups."""
        self.calls.append(("group_logs_by_timestamp", len(logs)))
        return []

    def get_live_reading(self, device_id: str) -> dict[str, Any]:
        """Record the call and return a copy of the device's canned reading."""
        self.calls.append(("get_live_reading", device_id))
        return dict(self.live.get(device_id, {}))


def _as_gateway(gw: RecordingGateway) -> DeviceGateway:
    """The recorder typed as the gateway it stands in for (it serves every call the sync makes)."""
    return cast("DeviceGateway", gw)


class SeedValues(TypedDict, total=False):
    """Reading values ``_seed`` writes (the timestamp comes from the age)."""

    soil_moisture: float
    temperature: float
    light: int
    env_humidity: float


@pytest.fixture
def cluster(tmp_db: IrrigationRepository, frozen_clock: Traveller) -> tuple[IrrigationRepository, int, int, int]:
    """Two sensors in one cluster (names sort Alpha < Beta), plus an unrelated cluster's sensor."""
    del frozen_clock  # requested only for its side effect
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
def spy_sync(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Record (sensor_id, hours) for every targeted sync, delegating to the real function."""
    calls: list[tuple[int, int]] = []
    real = sync_single_sensor  # the function services.sync calls (it imports it from greenhouse_core.sync)

    def _spy(db: IrrigationRepository, cloud: DeviceGateway, sensor: Sensor, hours: int) -> tuple[int, int, int]:
        calls.append((sensor.id, hours))
        return real(db, cloud, sensor, hours)

    monkeypatch.setattr(sync_service_module, "sync_single_sensor", _spy)
    return calls


def _seed(db: IrrigationRepository, sensor_id: int, age: int, **values: Unpack[SeedValues]) -> None:
    db.add_sensor_reading(sensor_id=sensor_id, timestamp=FROZEN_TS - age, **values)


def test_threshold_constant_is_four_hours() -> None:
    """Threshold constant is four hours."""
    assert timedelta(seconds=SENSOR_READING_STALE_SECONDS) == timedelta(hours=4)
    assert timedelta(hours=SNAPSHOT_LOOKBACK_HOURS) == timedelta(days=1)


def test_fresh_readings_cost_zero_gateway_calls(
    cluster: tuple[IrrigationRepository, int, int, int], spy_sync: list[tuple[int, int]]
) -> None:
    """Fresh readings cost zero gateway calls."""
    db, cid, alpha, beta = cluster
    _seed(db, alpha, 60, soil_moisture=40.0, temperature=20.0)
    _seed(db, beta, SENSOR_READING_STALE_SECONDS - 1, soil_moisture=35.0, temperature=24.0, light=800)
    gw = RecordingGateway()
    snapshot = SyncService(db, None, _as_gateway(gw)).ensure_fresh_and_read(cid)
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
def test_boundary_at_exactly_the_threshold(
    cluster: tuple[IrrigationRepository, int, int, int], spy_sync: list[tuple[int, int]], age: int, *, stale: bool
) -> None:
    """Boundary at exactly the threshold."""
    db, cid, alpha, beta = cluster
    _seed(db, alpha, age, soil_moisture=40.0)
    _seed(db, beta, 10, soil_moisture=50.0)
    gw = RecordingGateway()
    SyncService(db, None, _as_gateway(gw)).ensure_fresh_and_read(cid)
    assert spy_sync == ([(alpha, 6)] if stale else [])
    assert len(gw.calls) == (3 if stale else 0)


def test_stale_sensor_gets_exactly_one_targeted_sync(
    cluster: tuple[IrrigationRepository, int, int, int], spy_sync: list[tuple[int, int]]
) -> None:
    """Stale sensor gets exactly one targeted sync."""
    db, cid, alpha, beta = cluster
    stale_ts = FROZEN_TS - SENSOR_READING_STALE_SECONDS - 600
    _seed(db, alpha, SENSOR_READING_STALE_SECONDS + 600, soil_moisture=40.0)
    _seed(db, beta, 30, soil_moisture=50.0)
    gw = RecordingGateway(live={"dev_alpha": {"soil_moisture": 31.0, "temperature": 21.0}})
    snapshot = SyncService(db, None, _as_gateway(gw)).ensure_fresh_and_read(cid)
    assert spy_sync == [(alpha, 6)]
    assert gw.calls == [
        ("get_device_logs", "dev_alpha", (stale_ts - 60) * 1000),
        ("group_logs_by_timestamp", 0),
        ("get_live_reading", "dev_alpha"),
    ]
    # The freshly synced live row (persisted at now) is visible to the snapshot.
    assert snapshot == {"temperature": 21.0, "soil_moisture": 31.0, "env_humidity": None, "light": None}


def test_sensor_without_any_reading_is_synced_with_a_six_hour_window(
    cluster: tuple[IrrigationRepository, int, int, int], spy_sync: list[tuple[int, int]]
) -> None:
    """Sensor without any reading is synced with a six hour window."""
    db, cid, alpha, beta = cluster
    _seed(db, beta, 30, soil_moisture=50.0)
    live_soil = 44.0
    gw = RecordingGateway(live={"dev_alpha": {"soil_moisture": live_soil}})
    snapshot = SyncService(db, None, _as_gateway(gw)).ensure_fresh_and_read(cid)
    assert spy_sync == [(alpha, 6)]
    assert gw.calls[0] == ("get_device_logs", "dev_alpha", (FROZEN_TS - 6 * 3600) * 1000)
    assert snapshot is not None
    assert snapshot["soil_moisture"] == live_soil


def test_all_stale_sync_in_sensor_name_order(
    cluster: tuple[IrrigationRepository, int, int, int], spy_sync: list[tuple[int, int]]
) -> None:
    """All stale sync in sensor name order."""
    db, cid, alpha, beta = cluster
    gw = RecordingGateway()
    assert SyncService(db, None, _as_gateway(gw)).ensure_fresh_and_read(cid) is None
    assert spy_sync == [(alpha, 6), (beta, 6)]
    assert [c[1] for c in gw.calls if c[0] == "get_device_logs"] == ["dev_alpha", "dev_beta"]


def test_no_reading_at_all_and_no_cloud_returns_none_without_sync(
    cluster: tuple[IrrigationRepository, int, int, int], spy_sync: list[tuple[int, int]]
) -> None:
    """No reading at all and no cloud returns none without sync."""
    db, cid, _, _ = cluster
    assert SyncService(db, None, None).ensure_fresh_and_read(cid) is None
    assert spy_sync == []


def test_stale_but_no_cloud_reads_what_is_archived(
    cluster: tuple[IrrigationRepository, int, int, int], spy_sync: list[tuple[int, int]]
) -> None:
    """Stale but no cloud reads what is archived."""
    db, cid, alpha, _ = cluster
    _seed(db, alpha, SENSOR_READING_STALE_SECONDS + 1, soil_moisture=40.0, env_humidity=55.0)
    assert SyncService(db, None, None).ensure_fresh_and_read(cid) == {
        "temperature": None,
        "soil_moisture": 40.0,
        "env_humidity": 55.0,
        "light": None,
    }
    assert spy_sync == []


def test_archived_rows_older_than_the_lookback_are_ignored_by_the_snapshot(
    cluster: tuple[IrrigationRepository, int, int, int],
) -> None:
    """Archived rows older than the lookback are ignored by the snapshot."""
    db, cid, alpha, _ = cluster
    _seed(db, alpha, SNAPSHOT_LOOKBACK_HOURS * 3600 + 1, soil_moisture=40.0)
    assert SyncService(db, None, None).ensure_fresh_and_read(cid) is None


def test_failed_freshness_sync_is_swallowed_and_logged_at_debug(
    cluster: tuple[IrrigationRepository, int, int, int],
    spy_sync: list[tuple[int, int]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failed freshness sync is swallowed and logged at debug."""
    db, cid, alpha, beta = cluster
    _seed(db, beta, 30, soil_moisture=50.0)
    gw = RecordingGateway(logs_error=RuntimeError("token expired"))
    with caplog.at_level(logging.DEBUG, logger="greenhouse_server.services.sync"):
        snapshot = SyncService(db, None, _as_gateway(gw)).ensure_fresh_and_read(cid)
    assert snapshot == {"temperature": None, "soil_moisture": 50.0, "env_humidity": None, "light": None}
    assert spy_sync == [(alpha, 6)]
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("DEBUG", "Freshness sync failed for sensor Alpha")
    ]


def test_empty_cluster_returns_none_with_zero_calls(
    tmp_db: IrrigationRepository, spy_sync: list[tuple[int, int]]
) -> None:
    """Empty cluster returns none with zero calls."""
    cid = tmp_db.add_cluster("Empty")
    gw = RecordingGateway()
    assert SyncService(tmp_db, None, _as_gateway(gw)).ensure_fresh_and_read(cid) is None
    assert gw.calls == []
    assert spy_sync == []


class TestSyncAllSensors:
    """Tests for sync all sensors."""

    def test_without_cloud_returns_the_fixed_error_payload(self, tmp_db: IrrigationRepository) -> None:
        """Without cloud returns the fixed error payload."""
        assert SyncService(tmp_db, None, None).sync_all_sensors() == {
            "total_synced": 0,
            "total_new": 0,
            "total_live": 0,
            "errors": ["No cloud connection"],
        }

    def test_with_cloud_delegates_to_core_sync_with_hours(
        self, tmp_db: IrrigationRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With cloud delegates to core sync with hours."""
        seen: list[tuple[object, ...]] = []

        def fake_core_sync(repo: IrrigationRepository, cloud: DeviceGateway, hours: int) -> SyncStats:
            seen.append((repo, cloud, hours))
            return cast("SyncStats", {"ok": 1})  # a sentinel the service must pass through untouched

        monkeypatch.setattr(sync_service_module, "core_sync", fake_core_sync)
        gw = RecordingGateway()
        result: object = SyncService(tmp_db, None, _as_gateway(gw)).sync_all_sensors(hours=3)
        assert result == {"ok": 1}
        assert seen == [(tmp_db, gw, 3)]
