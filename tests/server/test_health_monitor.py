"""DeviceHealthMonitor transition matrix and engine actuation-gate tests.

Driven entirely off in-memory fakes so each case runs deterministically:
the fake irrigator/sensor adapters let us drive arbitrary
DeviceHealthState transitions via ``set_health``; a frozen clock lets us
exercise the OFFLINE_AFTER_MINUTES window without sleeping.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fake_devices import FakeIrrigatorAdapter, FakeSensorAdapter
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.models import (
    ENTITY_IRRIGATOR,
    Alert,
    Base,
    Irrigator,
    Sensor,
)
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.health_monitor import (
    LEGACY_PUMP_DRY_RUN_CODE,
    SOURCE_HEALTH,
    DeviceHealthMonitor,
)


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
        irrigator_type="tuya_cloud",
        config={},
    )
    sensor_id = repo.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="hm_sensor",
        name="HM Sensor",
        sensor_type="soil_moisture",
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
    # Aliases used by add_irrigator above: ``tuya_cloud``/``soil_moisture`` →
    # default keys. We register the default model_keys here so DeviceRegistry's
    # alias table resolves the fake adapters in this test.
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

        # Advance the clock beyond OFFLINE_AFTER_MINUTES (30 min default) and
        # report the same last_seen.
        clock.tick(31 * 60)
        sensor_adapter.set_health(_state(last_seen_ts=clock() - 31 * 60))
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


class TestLegacyMigration:
    """Open pump_dry_run alerts are auto-resolved on startup."""

    def test_migrates_open_legacy_alert(self, monitor, cluster_irrigator_sensor):
        monitor_, *_ = monitor
        irrigator, _ = cluster_irrigator_sensor

        # Simulate an existing legacy alert raised by the previous codebase.
        repo = monitor_._repo
        repo.upsert_alert(
            dedup_key="pump::pump_dry_run::1::irrigator1",
            source="pump",
            code=LEGACY_PUMP_DRY_RUN_CODE,
            title="Pump dry-run · legacy",
            message="legacy row",
            severity="critical",
            entity_type=ENTITY_IRRIGATOR,
            entity_id=irrigator.id,
            cluster_id=irrigator.cluster_id,
        )
        migrated = monitor_.migrate_legacy_pump_alerts()
        assert migrated == 1
        legacy = repo.session.scalar(select(Alert).where(Alert.dedup_key == "pump::pump_dry_run::1::irrigator1"))
        assert legacy.status == "resolved"


class TestBackfillFromHistory:
    """Persistent low-battery state survives a server restart via back-fill."""

    def test_n_consecutive_low_battery_readings_raise_alert(self, monitor, cluster_irrigator_sensor):
        monitor_, _, _, _ = monitor
        _, sensor = cluster_irrigator_sensor
        repo = monitor_._repo

        import time as _time

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

        import time as _time

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

    def test_pipeline_skips_with_typed_reason(
        self,
        repo,
        monitor,
        cluster_irrigator_sensor,
        monkeypatch,
    ):
        from unittest.mock import MagicMock

        from greenhouse_core.logic.decision import TriggerCode
        from greenhouse_server.services.irrigation import IrrigationService

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

        import time as _time

        now = int(_time.time())
        for offset in range(3):
            repo.add_sensor_reading(
                sensor_id=sensor.id,
                timestamp=now - offset * 600,
                soil_moisture=20.0,  # very dry, would normally trigger irrigation
                temperature=22.0,
            )
        repo.session.commit()

        # Reuse the fake adapter already registered on the monitor's registry
        # so the irrigation service resolves through the same fake.
        registry = monitor_._registry  # noqa: SLF001 — same registry as the monitor
        sync_service = MagicMock()
        sync_service.sync_and_read_sensors.return_value = {
            "temperature": 22.0,
            "soil_moisture": 20.0,
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
        # The blocked device must not have been actuated.
        assert not any(c[0] == "start" for c in irr_adapter.calls)
