"""DeviceHealthMonitor transition matrix and engine actuation-gate tests.

Driven entirely off in-memory fakes so each case runs deterministically:
the fake irrigator/sensor adapters let us drive arbitrary
DeviceHealthState transitions via ``set_health``; a frozen clock lets us
exercise the OFFLINE_AFTER_MINUTES window without sleeping.
"""

from __future__ import annotations

import time as _time
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fake_devices import FakeIrrigatorAdapter, FakeSensorAdapter
from greenhouse_core.constants import DEFAULT_SYNC_INTERVAL_MINUTES, SENSOR_OFFLINE_GRACE_MINUTES
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.logic.decision import TriggerCode
from greenhouse_core.models import (
    ENTITY_IRRIGATOR,
    SOURCE_HEALTH,
    Alert,
    Base,
    IrrigationEvent,
    Irrigator,
    Sensor,
)
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.health_monitor import (
    DeviceHealthMonitor,
    _Cached,
)
from greenhouse_server.services.irrigation import IrrigationService
from greenhouse_server.services.pump_watcher import EVENT_ACTION_ABORTED, PumpWatcherService


@pytest.fixture
def repo():
    engine = create_engine("sqlite://", echo=False)
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield IrrigationRepository(session)
    session.close()
    engine.dispose()


@pytest.fixture
def cluster_irrigator_sensor(repo) -> tuple[Irrigator, Sensor]:
    cluster_id = repo.add_cluster("HM Cluster")
    irrigator_id = repo.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id="hm_irrigator",
        name="HM Irrigator",
        irrigator_type="rainpoint.ik10pw",
        config={},
    )
    sensor_id = repo.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="hm_sensor",
        name="HM Sensor",
        sensor_type="tuya.tr301z",
        config={},
    )
    repo.session.commit()
    return repo.get_irrigator(irrigator_id), repo.get_sensor(sensor_id)


class _FrozenClock:
    """Manually-advanced clock so OFFLINE_AFTER_MINUTES tests don't sleep."""

    def __init__(self, *, t: int = 1_700_000_000) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t

    def tick(self, seconds: int) -> None:
        self.t += seconds


@pytest.fixture
def registry_with_fakes() -> tuple[DeviceRegistry, FakeIrrigatorAdapter, FakeSensorAdapter]:
    irr_adapter = FakeIrrigatorAdapter()
    sensor_adapter = FakeSensorAdapter()
    registry = DeviceRegistry()
    # The fakes are registered under the model keys the rows above carry.
    registry.register_irrigator("rainpoint.ik10pw", lambda: irr_adapter)
    registry.register_sensor("tuya.tr301z", lambda: sensor_adapter)
    return registry, irr_adapter, sensor_adapter


@pytest.fixture
def monitor(
    repo, registry_with_fakes
) -> tuple[DeviceHealthMonitor, FakeIrrigatorAdapter, FakeSensorAdapter, _FrozenClock]:
    registry, irr_adapter, sensor_adapter = registry_with_fakes
    clock = _FrozenClock()
    monitor = DeviceHealthMonitor(repo=repo, registry=registry, clock=clock)
    return monitor, irr_adapter, sensor_adapter, clock


def _state(
    *,
    alarms: frozenset[HealthAlarm] = frozenset(),
    battery_pct: int | None = None,
    offline: bool = False,
    last_seen_ts: int | None = None,
    observed_at: int = 1_700_000_000,
) -> DeviceHealthState:
    return DeviceHealthState(
        observed_at=observed_at,
        battery_pct=battery_pct,
        offline=offline,
        last_seen_ts=last_seen_ts,
        alarms=alarms,
    )


class TestTransitionMatrix:
    """clean → alarm → alarm → clean reproduces the documented behaviour."""

    def test_clean_to_alarm_raises_alert(self, monitor, cluster_irrigator_sensor):
        monitor_, irr_adapter, _, _ = monitor
        irrigator, _ = cluster_irrigator_sensor

        irr_adapter.set_health(_state(alarms=frozenset({HealthAlarm.NO_WATER})))
        derived = monitor_.poll_irrigator(irrigator)
        assert HealthAlarm.NO_WATER in derived.alarms

        alert = monitor_._repo.session.scalar(
            select(Alert).where(Alert.dedup_key == f"health:irrigator:{irrigator.id}:no_water")
        )
        assert alert is not None
        assert alert.status == "open"
        assert alert.source == SOURCE_HEALTH
        assert alert.severity == "critical"
        assert alert.occurrence_count == 1

    def test_alarm_to_alarm_increments_occurrence(self, monitor, cluster_irrigator_sensor):
        monitor_, irr_adapter, _, _ = monitor
        irrigator, _ = cluster_irrigator_sensor

        irr_adapter.set_health(_state(alarms=frozenset({HealthAlarm.NO_WATER})))
        monitor_.poll_irrigator(irrigator)
        monitor_.poll_irrigator(irrigator)

        alert = monitor_._repo.session.scalar(
            select(Alert).where(Alert.dedup_key == f"health:irrigator:{irrigator.id}:no_water")
        )
        # The diff machinery only re-raises on *appearance*; a sustained
        # alarm leaves the cached state unchanged so the alert row stays
        # at occurrence_count=1 from the original transition.
        assert alert.occurrence_count == 1
        assert alert.status == "open"

    def test_alarm_to_clean_resolves(self, monitor, cluster_irrigator_sensor):
        monitor_, irr_adapter, _, _ = monitor
        irrigator, _ = cluster_irrigator_sensor

        irr_adapter.set_health(_state(alarms=frozenset({HealthAlarm.NO_WATER})))
        monitor_.poll_irrigator(irrigator)
        irr_adapter.set_health(_state(alarms=frozenset()))
        monitor_.poll_irrigator(irrigator)

        alert = monitor_._repo.session.scalar(
            select(Alert).where(Alert.dedup_key == f"health:irrigator:{irrigator.id}:no_water")
        )
        assert alert.status == "resolved"

    def test_battery_low_then_critical_open_two_distinct_alerts(self, monitor, cluster_irrigator_sensor):
        monitor_, _, sensor_adapter, _ = monitor
        _, sensor = cluster_irrigator_sensor

        # 25% → no alarm (>= BATTERY_LOW_PCT = 20)
        sensor_adapter.set_health(_state(battery_pct=25))
        monitor_.poll_sensor(sensor)
        # 15% → LOW_BATTERY (between 20 and 5)
        sensor_adapter.set_health(_state(battery_pct=15))
        monitor_.poll_sensor(sensor)
        low_key = f"health:sensor:{sensor.id}:low_battery"
        low = monitor_._repo.session.scalar(select(Alert).where(Alert.dedup_key == low_key))
        assert low is not None and low.status == "open"

        # 3% → BATTERY_CRITICAL (and LOW_BATTERY no longer derived → resolved)
        sensor_adapter.set_health(_state(battery_pct=3))
        monitor_.poll_sensor(sensor)
        crit_key = f"health:sensor:{sensor.id}:battery_critical"
        crit = monitor_._repo.session.scalar(select(Alert).where(Alert.dedup_key == crit_key))
        assert crit is not None and crit.status == "open"
        assert crit.severity == "critical"

    def test_offline_transition_via_stale_last_seen(self, monitor, cluster_irrigator_sensor):
        monitor_, _, sensor_adapter, clock = monitor
        _, sensor = cluster_irrigator_sensor

        # First read: fresh last_seen, no alarms.
        sensor_adapter.set_health(_state(last_seen_ts=clock()))
        monitor_.poll_sensor(sensor)
        offline_key = f"health:sensor:{sensor.id}:device_offline"
        assert monitor_._repo.session.scalar(select(Alert).where(Alert.dedup_key == offline_key)) is None

        # Advance the clock beyond the sensor window (sync interval + grace, B-3)
        # and report the same last_seen.
        stale = (DEFAULT_SYNC_INTERVAL_MINUTES + SENSOR_OFFLINE_GRACE_MINUTES + 1) * 60
        clock.tick(stale)
        sensor_adapter.set_health(_state(last_seen_ts=clock() - stale))
        monitor_.poll_sensor(sensor)
        alert = monitor_._repo.session.scalar(select(Alert).where(Alert.dedup_key == offline_key))
        assert alert is not None and alert.status == "open"

    def test_explicit_offline_flag_opens_alert(self, monitor, cluster_irrigator_sensor):
        monitor_, irr_adapter, _, _ = monitor
        irrigator, _ = cluster_irrigator_sensor

        irr_adapter.set_health(_state(offline=True))
        monitor_.poll_irrigator(irrigator)
        alert = monitor_._repo.session.scalar(
            select(Alert).where(Alert.dedup_key == f"health:irrigator:{irrigator.id}:device_offline")
        )
        assert alert is not None and alert.status == "open"


class TestActuationGate:
    """is_actuation_blocked answers from the cache, never re-polls."""

    def test_no_cached_state_falls_open(self, monitor, cluster_irrigator_sensor):
        monitor_, _, _, _ = monitor
        irrigator, _ = cluster_irrigator_sensor

        blocked, alarms = monitor_.is_actuation_blocked(irrigator)
        assert blocked is False
        assert alarms == []

    def test_no_water_blocks(self, monitor, cluster_irrigator_sensor):
        monitor_, irr_adapter, _, _ = monitor
        irrigator, _ = cluster_irrigator_sensor

        irr_adapter.set_health(_state(alarms=frozenset({HealthAlarm.NO_WATER})))
        monitor_.poll_irrigator(irrigator)
        blocked, alarms = monitor_.is_actuation_blocked(irrigator)
        assert blocked is True
        assert HealthAlarm.NO_WATER in alarms

    def test_low_battery_does_not_block(self, monitor, cluster_irrigator_sensor):
        monitor_, irr_adapter, _, _ = monitor
        irrigator, _ = cluster_irrigator_sensor

        irr_adapter.set_health(_state(battery_pct=10))
        monitor_.poll_irrigator(irrigator)
        blocked, _ = monitor_.is_actuation_blocked(irrigator)
        # LOW_BATTERY is advisory, not actuation-blocking.
        assert blocked is False

    @staticmethod
    def _seed_cache(monitor_, irrigator, derived):
        """Put ``derived`` straight into the actuation-gate cache for ``irrigator``."""

        monitor_._cache[(ENTITY_IRRIGATOR, irrigator.id)] = _Cached(state=_state(), derived_alarms=derived)

    @pytest.mark.parametrize("alarm", list(HealthAlarm), ids=lambda a: a.value)
    def test_blocking_set_for_every_alarm(self, monitor, cluster_irrigator_sensor, alarm):
        """Exactly NO_WATER, RAIN_DETECTED and DEVICE_OFFLINE block; every other alarm is advisory."""
        monitor_, _, _, _ = monitor
        irrigator, _ = cluster_irrigator_sensor
        self._seed_cache(monitor_, irrigator, frozenset({alarm}))

        blocking = {HealthAlarm.NO_WATER, HealthAlarm.RAIN_DETECTED, HealthAlarm.DEVICE_OFFLINE}
        expected = (True, [alarm]) if alarm in blocking else (False, [])
        assert monitor_.is_actuation_blocked(irrigator) == expected

    def test_blocking_alarms_keep_derived_order(self, monitor, cluster_irrigator_sensor):
        """With every alarm cached, the blocking ones come back in derived-set iteration order."""
        monitor_, _, _, _ = monitor
        irrigator, _ = cluster_irrigator_sensor
        derived = frozenset(HealthAlarm)
        self._seed_cache(monitor_, irrigator, derived)

        blocking = {HealthAlarm.NO_WATER, HealthAlarm.RAIN_DETECTED, HealthAlarm.DEVICE_OFFLINE}
        assert monitor_.is_actuation_blocked(irrigator) == (True, [a for a in derived if a in blocking])


class TestBackfillFromHistory:
    """Persistent low-battery state survives a server restart via back-fill."""

    def test_n_consecutive_low_battery_readings_raise_alert(self, monitor, cluster_irrigator_sensor):
        monitor_, _, _, _ = monitor
        _, sensor = cluster_irrigator_sensor
        repo = monitor_._repo

        now = int(_time.time())
        for offset in range(5):
            repo.add_sensor_reading(
                sensor_id=sensor.id,
                timestamp=now - offset * 600,
                soil_moisture=42.0,
                battery_state="low",
            )
        repo.session.commit()

        monitor_.backfill_from_history()

        low_key = f"health:sensor:{sensor.id}:low_battery"
        alert = repo.session.scalar(select(Alert).where(Alert.dedup_key == low_key))
        assert alert is not None
        assert alert.status == "open"

    def test_short_history_does_not_backfill(self, monitor, cluster_irrigator_sensor):
        monitor_, _, _, _ = monitor
        _, sensor = cluster_irrigator_sensor
        repo = monitor_._repo

        now = int(_time.time())
        # Only 2 readings — below the SENSOR_HEALTH_BACKFILL_WINDOW of 5.
        for offset in range(2):
            repo.add_sensor_reading(
                sensor_id=sensor.id,
                timestamp=now - offset * 600,
                battery_state="low",
            )
        repo.session.commit()

        monitor_.backfill_from_history()

        low_key = f"health:sensor:{sensor.id}:low_battery"
        assert repo.session.scalar(select(Alert).where(Alert.dedup_key == low_key)) is None


class TestEngineActuationBlock:
    """run_irrigation_pipeline short-circuits to SKIP when monitor blocks."""

    @pytest.mark.parametrize(
        ("soil", "season"),
        [
            # Very dry → critical-stress early return (seasonal rule never runs).
            (20.0, None),
            # Moderately dry in winter → full pipeline incl. SEASONAL_HOLD. Regression:
            # the seasonal rule rebound ``reasons`` to a tuple, so this gate's
            # ``add_reason`` raised AttributeError instead of recording the block.
            (38.0, "winter"),
        ],
    )
    def test_pipeline_skips_with_typed_reason(
        self,
        repo,
        monitor,
        cluster_irrigator_sensor,
        monkeypatch,
        soil,
        season,
    ):
        monitor_, irr_adapter, _, _ = monitor
        irrigator, sensor = cluster_irrigator_sensor

        # Drive the monitor into a blocked state for this irrigator.
        irr_adapter.set_health(_state(alarms=frozenset({HealthAlarm.NO_WATER})))
        monitor_.poll_irrigator(irrigator)
        assert monitor_.is_actuation_blocked(irrigator)[0] is True

        # Plant + sensor reading so the engine returns a decision at all.
        plant_id = repo.add_plant(
            cluster_id=irrigator.cluster_id,
            species="Monstera",
            category="tropical",
            water_needs="medium",
            ideal_temp_min=18.0,
            ideal_temp_max=27.0,
            ideal_humidity_min=60.0,
            ideal_humidity_max=80.0,
        )
        repo.session.commit()
        # Place the sensor on the plant so the engine has data.
        repo.update_sensor(sensor.id, plant_id=plant_id)

        now = int(_time.time())
        for offset in range(3):
            repo.add_sensor_reading(
                sensor_id=sensor.id,
                timestamp=now - offset * 600,
                soil_moisture=soil,  # dry, would normally trigger irrigation
                temperature=22.0,
            )
        repo.session.commit()
        if season is not None:
            monkeypatch.setattr("greenhouse_core.logic.engine.season_for", lambda *_a, **_k: season)

        # Reuse the fake adapter already registered on the monitor's registry
        # so the irrigation service resolves through the same fake.
        registry = monitor_._registry  # noqa: SLF001 — same registry as the monitor
        sync_service = MagicMock()
        sync_service.ensure_fresh_and_read.return_value = {
            "temperature": 22.0,
            "soil_moisture": soil,
        }
        weather = MagicMock()
        weather.get_current.return_value = {"feels_like": 22.0}
        plant_db = MagicMock()
        plant_db.get_care_data.return_value = {
            "soil_moisture_target": "45-65",
            "ideal_temp": "18-27",
            "ideal_humidity": "60-80",
        }

        service = IrrigationService(
            repo=repo,
            registry=registry,
            sync_service=sync_service,
            weather_client=weather,
            plant_db=plant_db,
            health_monitor=monitor_,
        )

        result = service.run_irrigation_pipeline(irrigator.cluster_id)

        assert result["action"] == "skip"
        assert any(r["code"] == TriggerCode.DEVICE_NO_WATER.value for r in result["reasons"]), result["reasons"]
        if season is not None:
            assert any(r["code"] == TriggerCode.SEASONAL_HOLD.value for r in result["reasons"]), result["reasons"]
        # The blocked device must not have been actuated.
        assert not any(c[0] == "start" for c in irr_adapter.calls)


class TestSharedMonitorViews:
    """S6 fixed: each job and pump watcher writes through its own view of the shared monitor.

    Production wires ONE ``DeviceHealthMonitor`` (``app.state.health_monitor``). The pump-watcher
    job takes a view on its own session (``services/pump_watcher_jobs.py`` ``_run_pump_watcher``) and then
    watches for minutes; meanwhile ``_health_monitor_job`` / ``_build_irrigation_service``
    (``scheduler.py``) take views on *their* job sessions. The interleaving is reproduced
    deterministically from the watcher's ``sleep`` hook. It used to re-bind one shared ``_repo``,
    so the dry-run alert landed in the other job's session.

    Two SQLite files stand in for "the watcher's session" and "the other job's session" so the
    write can be attributed.
    """

    def test_pump_watcher_trip_writes_its_alert_through_its_own_view(self, tmp_path):
        def _engine(name: str):
            engine = create_engine(f"sqlite:///{tmp_path / name}", echo=False)
            Base.metadata.create_all(engine)
            return engine

        watcher_engine, other_engine = _engine("watcher.db"), _engine("other_job.db")
        watcher_repo = IrrigationRepository(Session(watcher_engine))
        other_repo = IrrigationRepository(Session(other_engine))

        cluster_id = watcher_repo.add_cluster("S6 Cluster")
        irrigator_id = watcher_repo.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id="fake_tuya_device_aabbccdd",
            name="S6 Irrigator",
            irrigator_type="rainpoint.ik10pw",
            config={},
        )
        watcher_repo.commit()
        irrigator = watcher_repo.get_irrigator(irrigator_id)

        adapter = FakeIrrigatorAdapter()
        registry = DeviceRegistry()
        registry.register_irrigator("rainpoint.ik10pw", lambda: adapter)
        shared = DeviceHealthMonitor(repo=other_repo, registry=registry)

        no_water = _state(alarms=frozenset({HealthAlarm.NO_WATER}))
        now = [0.0]
        other_views: list[DeviceHealthMonitor] = []

        def _sleep(_seconds: float) -> None:
            # Between two polls another scheduler job takes its own view; the pump then reports a dry run.
            other_views.append(shared.for_repo(other_repo))
            adapter.set_health(no_water)
            now[0] += 1.0

        watcher = PumpWatcherService(
            watcher_repo,
            registry,
            poll_seconds=1.0,
            warmup_seconds=0.0,
            clock=lambda: now[0],
            sleep=_sleep,
            monitor=shared.for_repo(watcher_repo),
        )

        result = watcher.watch(irrigator, duration_seconds=30, started_at=100)

        assert result["outcome"] == "tripped"
        key = f"health:{ENTITY_IRRIGATOR}:{irrigator_id}:no_water"

        # The watcher committed its own session: the aborted event AND the alert are durable together.
        with Session(watcher_engine) as fresh:
            assert fresh.scalars(select(IrrigationEvent.action)).all() == [EVENT_ACTION_ABORTED]
            alert = fresh.scalar(select(Alert).where(Alert.dedup_key == key))
            assert alert is not None and alert.status == "open"

        # Nothing leaked into the other job's session; its rollback cannot lose the alert.
        assert other_repo.get_open_alert_by_key(key) is None
        other_repo.rollback()

        # The cache is shared: every view (and the app-wide instance) sees the block.
        assert shared.is_actuation_blocked(irrigator) == (True, [HealthAlarm.NO_WATER])
        assert other_views[0].is_actuation_blocked(irrigator) == (True, [HealthAlarm.NO_WATER])

        watcher_repo.session.close()
        other_repo.session.close()
        watcher_engine.dispose()
        other_engine.dispose()


class TestUnraisedAlarmRetry:
    """S6 fixed: a NO_WATER whose alert write failed keeps blocking and is raised again later."""

    def test_failed_alert_write_still_blocks_and_retries_on_next_observation(
        self, monitor, cluster_irrigator_sensor, monkeypatch
    ):
        monitor_, irr_adapter, _, _ = monitor
        irrigator, _ = cluster_irrigator_sensor
        key = f"health:irrigator:{irrigator.id}:no_water"
        irr_adapter.set_health(_state(alarms=frozenset({HealthAlarm.NO_WATER})))

        real_upsert = IrrigationRepository.upsert_alert

        def locked(_self, *_args, **_kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(IrrigationRepository, "upsert_alert", locked)
        monitor_.poll_irrigator(irrigator)
        assert monitor_.is_actuation_blocked(irrigator) == (True, [HealthAlarm.NO_WATER])
        assert monitor_._repo.get_open_alert_by_key(key) is None

        monkeypatch.setattr(IrrigationRepository, "upsert_alert", real_upsert)
        monitor_.poll_irrigator(irrigator)  # same alarm, no transition — but its alert is owed
        assert monitor_._repo.get_open_alert_by_key(key) is not None
        monitor_.poll_irrigator(irrigator)  # landed now: not raised again
        assert monitor_._repo.get_open_alert_by_key(key).occurrence_count == 1

    def test_mark_unraised_requeues_an_alert_lost_to_a_rollback(self, monitor, cluster_irrigator_sensor):
        monitor_, irr_adapter, _, _ = monitor
        irrigator, _ = cluster_irrigator_sensor
        key = f"health:irrigator:{irrigator.id}:no_water"
        irr_adapter.set_health(_state(alarms=frozenset({HealthAlarm.NO_WATER})))
        monitor_.poll_irrigator(irrigator)
        monitor_._repo.rollback()  # the trip's transaction was lost after the write
        assert monitor_._repo.get_open_alert_by_key(key) is None
        monitor_.mark_unraised("irrigator", irrigator.id, HealthAlarm.NO_WATER)
        assert monitor_.is_actuation_blocked(irrigator) == (True, [HealthAlarm.NO_WATER])
        monitor_.poll_irrigator(irrigator)
        assert monitor_._repo.get_open_alert_by_key(key) is not None


class TestRestoreFromOpenAlerts:
    """B-9 fixed: after a restart the cache is rebuilt from the unresolved health alerts."""

    def _raise_no_water(self, repo, registry, irrigator, irr_adapter) -> None:
        irr_adapter.set_health(_state(alarms=frozenset({HealthAlarm.NO_WATER})))
        DeviceHealthMonitor(repo=repo, registry=registry).poll_irrigator(irrigator)
        repo.commit()

    def test_open_alert_blocks_at_once_and_resolves_when_cleared(
        self, repo, registry_with_fakes, cluster_irrigator_sensor
    ):
        registry, irr_adapter, _ = registry_with_fakes
        irrigator, _ = cluster_irrigator_sensor
        self._raise_no_water(repo, registry, irrigator, irr_adapter)

        restarted = DeviceHealthMonitor(repo=repo, registry=registry)  # empty memory, as after a restart
        assert restarted.is_actuation_blocked(irrigator) == (False, [])
        assert restarted.restore_from_open_alerts() == 1
        assert restarted.is_actuation_blocked(irrigator) == (True, [HealthAlarm.NO_WATER])

        irr_adapter.set_health(_state())  # refilled while the server was down
        restarted.poll_irrigator(irrigator)
        key = f"health:irrigator:{irrigator.id}:no_water"
        assert repo.get_open_alert_by_key(key) is None
        assert restarted.is_actuation_blocked(irrigator) == (False, [])

    def test_persisting_alarm_is_not_raised_again(self, repo, registry_with_fakes, cluster_irrigator_sensor):
        registry, irr_adapter, _ = registry_with_fakes
        irrigator, _ = cluster_irrigator_sensor
        self._raise_no_water(repo, registry, irrigator, irr_adapter)
        restarted = DeviceHealthMonitor(repo=repo, registry=registry)
        restarted.restore_from_open_alerts()
        restarted.poll_irrigator(irrigator)  # still dry: no transition
        alert = repo.get_open_alert_by_key(f"health:irrigator:{irrigator.id}:no_water")
        assert (alert.status, alert.occurrence_count) == ("open", 1)

    def test_unknown_codes_and_resolved_alerts_are_ignored(self, repo, registry_with_fakes, cluster_irrigator_sensor):
        registry, _, _ = registry_with_fakes
        irrigator, _ = cluster_irrigator_sensor
        repo.upsert_alert(
            "health:irrigator:1:retired", "health", "retired_alarm", "t", "m", entity_type="irrigator", entity_id=1
        )
        resolved = repo.upsert_alert(
            "health:irrigator:1:no_water", "health", "no_water", "t", "m", entity_type="irrigator", entity_id=1
        )
        repo.resolve_alert(resolved.id)
        monitor_ = DeviceHealthMonitor(repo=repo, registry=registry)
        assert monitor_.restore_from_open_alerts() == 0
        assert monitor_.is_actuation_blocked(irrigator) == (False, [])


class TestTripOnTopOfACachedAlarm:
    """S5 follow-up: a failed alert insert during a trip cannot leave NO_WATER out of the cache."""

    def test_failed_insert_poisoning_the_session_still_caches_and_reraises_no_water(
        self, repo, registry_with_fakes, cluster_irrigator_sensor, monkeypatch
    ):
        registry, irr_adapter, _ = registry_with_fakes
        irrigator, _ = cluster_irrigator_sensor
        shared = DeviceHealthMonitor(repo=repo, registry=registry)
        irr_adapter.set_health(_state(offline=True))
        shared.poll_irrigator(irrigator)  # DEVICE_OFFLINE cached, alert open
        repo.commit()

        real_upsert = IrrigationRepository.upsert_alert

        def _poisoning_insert(self, *_args, **_kwargs):
            self.session.add(Alert(dedup_key=None, source="health", code="x", title="t", message="m"))
            self.session.flush()  # IntegrityError: the session now needs a rollback

        monkeypatch.setattr(IrrigationRepository, "upsert_alert", _poisoning_insert)
        irr_adapter.set_health(_state(alarms=frozenset({HealthAlarm.NO_WATER})))
        watcher = PumpWatcherService(
            repo, registry, warmup_seconds=0, clock=lambda: 0.0, sleep=lambda _s: None, monitor=shared.for_repo(repo)
        )
        assert watcher.watch(irrigator, 60, started_at=1_700_000_000)["outcome"] == "tripped"
        assert shared.is_actuation_blocked(irrigator) == (True, [HealthAlarm.NO_WATER])

        monkeypatch.setattr(IrrigationRepository, "upsert_alert", real_upsert)
        shared.poll_irrigator(irrigator)  # still dry: the owed alert is raised now
        repo.commit()
        assert repo.get_open_alert_by_key(f"health:irrigator:{irrigator.id}:no_water") is not None

    def test_mark_unraised_adds_an_alarm_the_cache_never_saw(self, repo, registry_with_fakes, cluster_irrigator_sensor):
        registry, _, _ = registry_with_fakes
        irrigator, _ = cluster_irrigator_sensor
        monitor_ = DeviceHealthMonitor(repo=repo, registry=registry)
        monitor_.mark_unraised("irrigator", irrigator.id, HealthAlarm.NO_WATER)
        assert monitor_.is_actuation_blocked(irrigator) == (True, [HealthAlarm.NO_WATER])
