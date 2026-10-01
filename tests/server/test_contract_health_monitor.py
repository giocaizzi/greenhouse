"""Characterization: ``DeviceHealthMonitor`` derives sensor health from SQLite (gap I8c).

Invariant #8: the health monitor reads the latest persisted ``SensorReading`` and
hands it to ``adapter.read_health(sensor, latest)`` — **no live Cloud read**. These
tests drive the *real* ``TR301ZAdapter`` over a ``DeviceGateway`` whose Cloud client
fails the test on any call, plus spy/fake adapters, and pin:

- battery / water-warning / offline-by-staleness transitions and the alert rows
  raised and resolved (dedup keys, titles, messages, severities, payloads);
- notifications for genuinely new alerts;
- the actuation gate and the history back-fill;
- suspected bug B-3 (sensors flap DEVICE_OFFLINE ~30 min after every 180-min sync),
  reproduced here and pinned as current behavior.

Frozen sets are sorted before comparison — their iteration order depends on the
per-process hash seed, so alert *ids* are never asserted, only dedup keys.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import pytest
from sqlalchemy import select

from fake_devices import FakeIrrigatorAdapter, FakeSensorAdapter
from golden import FROZEN_INSTANT, FROZEN_TS, assert_golden_json
from greenhouse_core.constants import OFFLINE_AFTER_MINUTES
from greenhouse_core.devices import DeviceGateway, DeviceRegistry, TR301ZAdapter
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.models import ENTITY_IRRIGATOR, ENTITY_SENSOR, Alert
from greenhouse_server.services.health_monitor import DeviceHealthMonitor

GOLDEN = "ingress_devices"
SYNC_INTERVAL_SECONDS = 180 * 60  # Settings.sync_interval_minutes default


class NoCloud:
    """A ``tinytuya.Cloud`` stand-in that fails the test on *any* attribute access."""

    def __init__(self) -> None:
        self.touched: list[str] = []

    def __getattr__(self, name):
        self.touched.append(name)
        raise AssertionError(f"health monitor touched the Cloud: {name}")


class SpyTR301Z(TR301ZAdapter):
    """Real TR-301Z health derivation, recording which persisted row it was handed."""

    seen: list = []

    def read_live(self, sensor):  # pragma: no cover — must never run
        raise AssertionError("health poll issued a live read")

    def read_health(self, sensor, latest=None):
        SpyTR301Z.seen.append(None if latest is None else latest.timestamp)
        return super().read_health(sensor, latest)


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def notify_alert(self, *, severity, title, message):
        self.sent.append({"severity": severity, "title": title, "message": message})
        return True


@pytest.fixture
def world(tmp_db, frozen_clock):
    cid = tmp_db.add_cluster("Health Cluster")
    irr_id = tmp_db.add_irrigator(
        cluster_id=cid, tuya_device_id="hm_pump", name="Pump One", irrigator_type="tuya_cloud", config={}
    )
    sensor_id = tmp_db.add_sensor(
        cluster_id=cid, tuya_device_id="hm_probe", name="Probe One", sensor_type="soil_moisture", config={}
    )
    tmp_db.session.commit()

    cloud = NoCloud()
    gateway = DeviceGateway("fake_client_id_for_tests", "fake_client_secret_for_tests", raw=cloud)
    irr_adapter = FakeIrrigatorAdapter()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda: irr_adapter)
    registry.register_sensor("tuya.tr301z", lambda: SpyTR301Z(gateway))
    SpyTR301Z.seen = []
    notifier = RecordingNotifier()
    monitor = DeviceHealthMonitor(repo=tmp_db, registry=registry, notifier=notifier)
    return {
        "repo": tmp_db,
        "cluster_id": cid,
        "irrigator": tmp_db.get_irrigator(irr_id),
        "sensor": tmp_db.get_sensor(sensor_id),
        "cloud": cloud,
        "irr_adapter": irr_adapter,
        "monitor": monitor,
        "notifier": notifier,
        "clock": frozen_clock,
    }


def _alerts(repo) -> list[dict]:
    rows = repo.session.scalars(select(Alert)).all()
    out = [
        {
            "dedup_key": a.dedup_key,
            "source": a.source,
            "code": a.code,
            "severity": a.severity,
            "status": a.status,
            "entity_type": a.entity_type,
            "entity_id": a.entity_id,
            "cluster_id": a.cluster_id,
            "title": a.title,
            "message": a.message,
            "occurrence_count": a.occurrence_count,
            "payload": json.loads(a.payload_json) if a.payload_json else None,
        }
        for a in rows
    ]
    return sorted(out, key=lambda a: a["dedup_key"])


def _move(world, seconds_after_frozen: int) -> int:
    world["clock"].move_to(FROZEN_INSTANT + timedelta(seconds=seconds_after_frozen))
    return FROZEN_TS + seconds_after_frozen


def _persist(world, ts: int, **values) -> None:
    world["repo"].add_sensor_reading(sensor_id=world["sensor"].id, timestamp=ts, **values)
    world["repo"].session.commit()


def _poll_sensor(world) -> list[str]:
    state = world["monitor"].poll_sensor(world["sensor"])
    derived = world["monitor"]._derive_alarms(state)
    return sorted(a.value for a in derived)


# ── No live Cloud read ────────────────────────────────────────────────────────


def test_poll_sensor_hands_the_newest_persisted_row_and_never_touches_the_cloud(world):
    for age, battery in ((3600, "high"), (60, "low"), (1800, "middle")):
        _persist(world, FROZEN_TS - age, soil_moisture=40.0, battery_state=battery)
    state = world["monitor"].poll_sensor(world["sensor"])
    assert SpyTR301Z.seen == [FROZEN_TS - 60]
    assert state.battery_pct == 10 and state.last_seen_ts == FROZEN_TS - 60
    assert world["cloud"].touched == []


def test_poll_all_with_fake_adapters_issues_no_read_live(tmp_db, frozen_clock):
    cid = tmp_db.add_cluster("C")
    tmp_db.add_irrigator(cluster_id=cid, tuya_device_id="p", name="P", irrigator_type="tuya_cloud", config={})
    s1 = tmp_db.add_sensor(cluster_id=cid, tuya_device_id="s1", name="S1", sensor_type="soil_moisture", config={})
    s2 = tmp_db.add_sensor(cluster_id=cid, tuya_device_id="s2", name="S2", sensor_type="soil_moisture", config={})
    tmp_db.session.commit()
    irr, sensor = FakeIrrigatorAdapter(), FakeSensorAdapter()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda: irr)
    registry.register_sensor("tuya.tr301z", lambda: sensor)
    DeviceHealthMonitor(repo=tmp_db, registry=registry).poll_all()
    assert sensor.calls == [("read_health", s1), ("read_health", s2)]
    assert [c[0] for c in irr.calls] == ["read_health"]


# ── Transition scenario (real TR-301Z) ────────────────────────────────────────


def test_sensor_transition_scenario_golden(world):
    """Battery → water-warning → recovery → staleness → recovery, with the alert rows after each poll."""
    steps = []

    def step(name: str, at: int, **reading):
        now = _move(world, at)
        if reading:
            _persist(world, now, soil_moisture=42.0, **reading)
        derived = _poll_sensor(world)
        steps.append(
            {
                "step": name,
                "now": now,
                "derived_alarms": derived,
                "alerts": _alerts(world["repo"]),
                "notifications": list(world["notifier"].sent),
            }
        )

    step("healthy", 0, battery_state="middle", water_warning=False)
    step("battery_low", 600, battery_state="low", water_warning=False)
    step("water_warning_on_top", 1200, battery_state="low", water_warning=True)
    step("all_recovered", 1800, battery_state="high", water_warning=False)
    step("stale_exactly_30min", 1800 + OFFLINE_AFTER_MINUTES * 60)
    step("stale_30min_plus_1s", 1800 + OFFLINE_AFTER_MINUTES * 60 + 1)
    step("back_online", 3700, battery_state="high", water_warning=False)
    assert world["cloud"].touched == []
    assert_golden_json(f"{GOLDEN}/health_monitor_sensor_transitions.json", steps)


def test_no_persisted_reading_is_offline(world):
    assert _poll_sensor(world) == ["device_offline"]
    (alert,) = _alerts(world["repo"])
    assert alert["dedup_key"] == f"health:sensor:{world['sensor'].id}:device_offline"
    assert alert["payload"] == {
        "entity_type": "sensor",
        "entity_id": world["sensor"].id,
        "alarm": "device_offline",
        "observed_at": FROZEN_TS,
        "battery_pct": None,
        "signal_quality": None,
        "last_seen_ts": None,
        "offline": True,
    }


def test_sensor_offline_flap_current_behavior_flags_offline_31min_after_every_sync(world):
    """Pins current (buggy) behavior: B-3 — see REFACTOR_NOTES.md.

    The offline window (``OFFLINE_AFTER_MINUTES`` = 30) is applied to the latest
    *persisted* reading, but the sync job persists readings only every 180 min.
    So a perfectly healthy sensor is flagged DEVICE_OFFLINE ~30 min after each
    sync, resolved by the next sync, and flagged again 30 min later — a re-opened
    alert whose ``occurrence_count`` climbs (and, per ``notify_if_new_alert``,
    notifies only the first time).
    """
    key = f"health:sensor:{world['sensor'].id}:device_offline"
    history = []
    for cycle in range(2):
        sync_at = cycle * SYNC_INTERVAL_SECONDS
        _persist(world, _move(world, sync_at), soil_moisture=45.0, battery_state="high")  # the sync job's row
        for minutes in (0, 30, 31, 179):
            _move(world, sync_at + minutes * 60)
            history.append((cycle, minutes, "device_offline" in _poll_sensor(world)))
    assert history == [
        (0, 0, False),
        (0, 30, False),
        (0, 31, True),
        (0, 179, True),
        (1, 0, False),
        (1, 30, False),
        (1, 31, True),
        (1, 179, True),
    ]
    alert = next(a for a in _alerts(world["repo"]) if a["dedup_key"] == key)
    assert (alert["status"], alert["occurrence_count"]) == ("open", 2)
    assert [n["title"] for n in world["notifier"].sent] == ["Device Offline · Probe One"]


# ── Alarm derivation thresholds ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("state_kwargs", "expected"),
    [
        ({"battery_pct": 20}, []),
        ({"battery_pct": 19}, ["low_battery"]),
        ({"battery_pct": 5}, ["low_battery"]),
        ({"battery_pct": 4}, ["battery_critical"]),
        ({"battery_pct": 0}, ["battery_critical"]),
        ({"signal_quality": 30}, []),
        ({"signal_quality": 29}, ["signal_loss"]),
        ({"last_seen_ts": FROZEN_TS - 1800}, []),
        ({"last_seen_ts": FROZEN_TS - 1801}, ["device_offline"]),
        ({"last_seen_ts": FROZEN_TS + 999}, []),  # future timestamp is "fresh"
        ({"offline": True, "last_seen_ts": FROZEN_TS}, ["device_offline"]),
        ({"alarms": frozenset({HealthAlarm.RAIN_DETECTED}), "battery_pct": 3}, ["battery_critical", "rain_detected"]),
    ],
)
def test_derived_alarm_thresholds(world, state_kwargs, expected):
    state = DeviceHealthState(observed_at=FROZEN_TS, **state_kwargs)
    derived = world["monitor"].record(ENTITY_SENSOR, world["sensor"].id, state)
    assert sorted(a.value for a in derived) == expected


def test_every_alarm_alert_row_golden(world):
    """Title / message / severity / payload for every HealthAlarm, raised on an irrigator."""
    state = DeviceHealthState(
        observed_at=FROZEN_TS,
        battery_pct=15,
        signal_quality=80,
        last_seen_ts=FROZEN_TS - 10,
        alarms=frozenset(HealthAlarm),
    )
    world["monitor"].record(ENTITY_IRRIGATOR, world["irrigator"].id, state)
    assert_golden_json(
        f"{GOLDEN}/health_monitor_alarm_rows.json",
        {"alerts": _alerts(world["repo"]), "notifications": sorted(world["notifier"].sent, key=lambda n: n["title"])},
    )


# ── Gate, inference, failure paths ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("alarms", "blocking"),
    [
        (frozenset(), []),
        (frozenset({HealthAlarm.NO_WATER}), ["no_water"]),
        (frozenset({HealthAlarm.RAIN_DETECTED}), ["rain_detected"]),
        (frozenset({HealthAlarm.LOW_BATTERY, HealthAlarm.SENSOR_FAULT, HealthAlarm.SIGNAL_LOSS}), []),
        (frozenset(HealthAlarm), ["device_offline", "no_water", "rain_detected"]),
    ],
)
def test_actuation_gate_blocking_set(world, alarms, blocking):
    world["irr_adapter"].set_health(DeviceHealthState(observed_at=FROZEN_TS, alarms=alarms))
    world["monitor"].poll_irrigator(world["irrigator"])
    blocked, found = world["monitor"].is_actuation_blocked(world["irrigator"])
    assert blocked is bool(blocking)
    assert sorted(a.value for a in found) == blocking


def test_sensor_state_never_gates_the_irrigator(world):
    world["monitor"].record(ENTITY_SENSOR, world["irrigator"].id, DeviceHealthState(observed_at=0, offline=True))
    assert world["monitor"].is_actuation_blocked(world["irrigator"]) == (False, [])


def test_unknown_sensor_model_is_not_recorded(world):
    repo = world["repo"]
    sid = repo.add_sensor(
        cluster_id=world["cluster_id"], tuya_device_id="x", name="Odd", sensor_type="acme.probe", config={}
    )
    repo.session.commit()
    state = world["monitor"].poll_sensor(repo.get_sensor(sid))
    assert state == DeviceHealthState(observed_at=FROZEN_TS, offline=False)
    assert (ENTITY_SENSOR, sid) not in world["monitor"]._cache
    assert _alerts(repo) == []


def test_record_infers_label_and_cluster_or_falls_back(world):
    monitor = world["monitor"]
    offline = DeviceHealthState(observed_at=FROZEN_TS, offline=True)
    monitor.record(ENTITY_IRRIGATOR, world["irrigator"].id, offline)
    monitor.record("widget", 5, offline)
    monitor.record(ENTITY_SENSOR, 999, offline)
    rows = {a["dedup_key"]: (a["title"], a["cluster_id"]) for a in _alerts(world["repo"])}
    assert rows == {
        f"health:irrigator:{world['irrigator'].id}:device_offline": ("Device Offline · Pump One", world["cluster_id"]),
        "health:widget:5:device_offline": ("Device Offline · widget#5", None),
        "health:sensor:999:device_offline": ("Device Offline · sensor#999", None),
    }


def test_explicit_label_and_cluster_win(world):
    world["monitor"].record(
        ENTITY_IRRIGATOR,
        world["irrigator"].id,
        DeviceHealthState(observed_at=FROZEN_TS, offline=True),
        label="Override",
        cluster_id=77,
    )
    (alert,) = _alerts(world["repo"])
    assert (alert["title"], alert["cluster_id"]) == ("Device Offline · Override", 77)


def test_poll_all_logs_and_continues_past_failures(world, caplog):
    def _boom(_irrigator):
        raise ConnectionError("no route")

    world["irr_adapter"].read_health = _boom  # type: ignore[method-assign]
    _persist(world, FROZEN_TS, soil_moisture=40.0, battery_state="low")
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.services.health_monitor"):
        world["monitor"].poll_all()
    assert caplog.messages == [f"Health poll failed for irrigator {world['irrigator'].id}"]
    assert [a["code"] for a in _alerts(world["repo"])] == ["low_battery"]


def test_alert_write_failure_is_logged_and_cache_still_updates(world, monkeypatch, caplog):
    def _fail(**_kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr(world["repo"], "upsert_alert", _fail)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.services.health_monitor"):
        world["monitor"].record(
            ENTITY_IRRIGATOR, world["irrigator"].id, DeviceHealthState(observed_at=FROZEN_TS, offline=True)
        )
    assert caplog.messages == [f"Failed to raise health alert device_offline for irrigator {world['irrigator'].id}"]
    assert world["monitor"].is_actuation_blocked(world["irrigator"])[0] is True


def test_sustained_alarm_does_not_touch_the_row_again(world):
    state = DeviceHealthState(observed_at=FROZEN_TS, offline=True)
    world["monitor"].record(ENTITY_IRRIGATOR, world["irrigator"].id, state)
    world["monitor"].record(ENTITY_IRRIGATOR, world["irrigator"].id, state)
    (alert,) = _alerts(world["repo"])
    assert alert["occurrence_count"] == 1
    assert len(world["notifier"].sent) == 1


def test_resolve_without_an_open_row_is_a_noop(world):
    monitor = world["monitor"]
    iid = world["irrigator"].id
    monitor.record(ENTITY_IRRIGATOR, iid, DeviceHealthState(observed_at=FROZEN_TS, offline=True))
    world["repo"].session.query(Alert).delete()
    monitor.record(ENTITY_IRRIGATOR, iid, DeviceHealthState(observed_at=FROZEN_TS))
    assert _alerts(world["repo"]) == []
    assert monitor.is_actuation_blocked(world["irrigator"]) == (False, [])


# ── Back-fill from history ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rows", "expected_codes"),
    [
        ([{"battery_state": " LOW "}] * 5, ["low_battery"]),
        ([{"water_warning": True}] * 5, ["sensor_fault"]),
        ([{"battery_state": "low", "water_warning": True}] * 5, ["low_battery", "sensor_fault"]),
        ([{"battery_state": "low"}] * 4, []),  # fewer than SENSOR_HEALTH_BACKFILL_WINDOW
        ([{"battery_state": "low"}] * 4 + [{"battery_state": "middle"}], []),
        ([{"battery_state": "low"}] * 5 + [{"battery_state": "middle"}], ["low_battery"]),  # only newest 5 count
        ([{"battery_state": "middle"}] + [{"battery_state": "low"}] * 5, []),  # newest is not low
        ([{"water_warning": None}] * 5, []),
    ],
)
def test_backfill_from_history_windows(world, rows, expected_codes):
    for i, values in enumerate(rows):  # i=0 is the newest row
        _persist(world, FROZEN_TS - 600 * (i + 1), soil_moisture=40.0, **values)
    world["monitor"].backfill_from_history()
    assert [a["code"] for a in _alerts(world["repo"])] == expected_codes


def test_backfill_ignores_rows_older_than_a_week_and_never_duplicates(world):
    for i in range(5):
        _persist(world, FROZEN_TS - 7 * 24 * 3600 - 60 * (i + 1), battery_state="low")
    world["monitor"].backfill_from_history()
    assert _alerts(world["repo"]) == []
    for i in range(5):
        _persist(world, FROZEN_TS - 60 * (i + 1), battery_state="low")
    world["monitor"].backfill_from_history()
    world["monitor"].backfill_from_history()
    (alert,) = _alerts(world["repo"])
    assert (alert["code"], alert["occurrence_count"], alert["message"]) == (
        "low_battery",
        1,
        "'Probe One' battery is low (None%). Replace soon to keep readings flowing.",
    )
    assert alert["payload"]["observed_at"] == FROZEN_TS
    # Back-fill writes the alert but not the cache: the next poll still sees a transition.
    assert (ENTITY_SENSOR, world["sensor"].id) not in world["monitor"]._cache
