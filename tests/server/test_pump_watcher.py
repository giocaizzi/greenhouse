"""Tests for the pump dry-run watcher.

The watcher polls DP 105 over the Tuya local protocol during an active
irrigation and aborts the pump on the first positive reading. Tests inject a
fake device manager (in lieu of tinytuya) plus a fake clock/sleep so each
case runs instantly.
"""

import time as _time
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.models import Base, Irrigator
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import SOURCE_PUMP
from greenhouse_server.services.health_monitor import SOURCE_HEALTH, DeviceHealthMonitor
from greenhouse_server.services.pump_watcher import (
    ALERT_CODE,
    EVENT_ACTION_ABORTED,
    PumpWatcherService,
)


def _make_monitor(repo: IrrigationRepository) -> DeviceHealthMonitor:
    """Build a no-registry monitor for tests — record() doesn't need a registry."""
    return DeviceHealthMonitor(repo=repo, registry=DeviceRegistry())


@pytest.fixture
def repo():
    engine = create_engine("sqlite://", echo=False)
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield IrrigationRepository(session)
    session.close()
    engine.dispose()


@pytest.fixture
def irrigator(repo):
    cluster_id = repo.add_cluster("Test Cluster")
    irrigator_id = repo.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id="fake_irrigator_pump",
        name="Pump Irrigator",
        irrigator_type="tuya_cloud",
        config={},
    )
    repo.session.commit()
    return repo.get_irrigator(irrigator_id)


class FakeClock:
    """Manually-advanced clock paired with a no-op sleep that records calls."""

    def __init__(self, *, step: float = 1.0):
        self.now = 0.0
        self.step = step
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += self.step


def _alarm(no_water: bool, alarm_raw: object = None, error: str | None = None) -> DeviceHealthState:
    """Build a DeviceHealthState matching the watcher's read_health contract."""
    if error is not None:
        return DeviceHealthState(
            observed_at=int(_time.time()),
            offline=True,
            alarms=frozenset(),
            raw={"error": error},
        )
    return DeviceHealthState(
        observed_at=int(_time.time()),
        offline=False,
        alarms=frozenset({HealthAlarm.NO_WATER}) if no_water else frozenset(),
        raw={
            "alarm_raw": alarm_raw,
            "running": True,
            "left_time": 60,
            "work_status": 2,
            "source": "local",
        },
    )


def _make_dm(reads: list[DeviceHealthState]) -> MagicMock:
    dm = MagicMock()
    dm.read_irrigator_health.side_effect = list(reads)
    dm.irrigator_off.return_value = (True, "Stopped OK")
    return dm


def _completed_alarms() -> list[DeviceHealthState]:
    # Plenty of "all clear" reads so the watcher can run until its deadline.
    return [_alarm(no_water=False, alarm_raw=0) for _ in range(50)]


class TestWatcherHappyPaths:
    def test_completes_when_alarm_never_fires(self, repo, irrigator):
        """No alarm bit set across the cycle → outcome=completed, pump untouched."""
        clock = FakeClock(step=1.0)
        dm = _make_dm(_completed_alarms())
        watcher = PumpWatcherService(
            repo,
            dm,
            poll_seconds=1.0,
            warmup_seconds=0.0,
            max_read_failures=5,
            clock=clock,
            sleep=clock.sleep,
        )

        result = watcher.watch(irrigator, duration_seconds=5)

        assert result["outcome"] == "completed"
        dm.irrigator_off.assert_not_called()
        # No abort events should have been recorded
        events = repo.get_recent_events(irrigator.id, hours=1)
        assert all(e.action != EVENT_ACTION_ABORTED for e in events)
        assert repo.count_open_alerts() == 0

    def test_completes_when_duration_is_zero(self, repo, irrigator):
        """Duration 0 means there's nothing to watch — exit immediately."""
        clock = FakeClock(step=1.0)
        dm = _make_dm(_completed_alarms())
        watcher = PumpWatcherService(
            repo,
            dm,
            poll_seconds=1.0,
            warmup_seconds=0.0,
            clock=clock,
            sleep=clock.sleep,
        )

        result = watcher.watch(irrigator, duration_seconds=0)

        assert result["outcome"] == "completed"
        assert result["polls"] == 0
        dm.read_irrigator_health.assert_not_called()


class TestWatcherTrips:
    def test_trips_on_alarm_after_warmup(self, repo, irrigator):
        """Alarm fires mid-cycle → pump stopped, alert raised, event logged."""
        clock = FakeClock(step=1.0)
        # 3 clean reads, then alarm. With step=1s and warmup=0, the alarm hits
        # at t≈3s and trips immediately.
        reads = [
            _alarm(no_water=False, alarm_raw=0),
            _alarm(no_water=False, alarm_raw=0),
            _alarm(no_water=False, alarm_raw=0),
            _alarm(no_water=True, alarm_raw=1),
        ] + _completed_alarms()
        dm = _make_dm(reads)
        monitor = _make_monitor(repo)
        watcher = PumpWatcherService(
            repo,
            dm,
            poll_seconds=1.0,
            warmup_seconds=0.0,
            clock=clock,
            sleep=clock.sleep,
            monitor=monitor,
        )

        result = watcher.watch(irrigator, duration_seconds=30, started_at=100)

        assert result["outcome"] == "tripped"
        assert result["alarm_raw"] == 1
        dm.irrigator_off.assert_called_once_with(irrigator)

        # Abort event recorded
        events = repo.get_recent_events(irrigator.id, hours=1)
        aborted = [e for e in events if e.action == EVENT_ACTION_ABORTED]
        assert len(aborted) == 1
        assert aborted[0].triggered_by == "pump_watcher"

        # Critical alert raised under SOURCE_HEALTH / no_water — unified
        # dedup_key replaces the legacy pump_dry_run alias.
        open_alerts = repo.list_alerts(limit=10)
        health_alerts = [a for a in open_alerts if a.source == SOURCE_HEALTH and a.code == ALERT_CODE]
        assert len(health_alerts) == 1
        assert health_alerts[0].severity == "critical"
        assert health_alerts[0].cluster_id == irrigator.cluster_id
        assert health_alerts[0].dedup_key == f"health:irrigator:{irrigator.id}:no_water"

    def test_respects_warmup_window(self, repo, irrigator):
        """Alarm bit set from t=0 must not trip until warmup elapses."""
        clock = FakeClock(step=1.0)
        # Every read shows the alarm bit
        dm = _make_dm([_alarm(no_water=True, alarm_raw=1) for _ in range(20)])
        watcher = PumpWatcherService(
            repo,
            dm,
            poll_seconds=1.0,
            warmup_seconds=5.0,
            clock=clock,
            sleep=clock.sleep,
        )

        result = watcher.watch(irrigator, duration_seconds=30, started_at=200)

        assert result["outcome"] == "tripped"
        # First trip-eligible poll is the one taken at clock >= warmup (5s).
        # With step=1s per sleep, polls 1..5 are warm-up (still ignored), the
        # 6th poll at clock>=5 trips. So polls should be >= 6.
        assert result["polls"] >= 6

    def test_trip_records_payload_with_alarm_value(self, repo, irrigator):
        """The activity event records the raw DP 105 value for forensics."""
        clock = FakeClock(step=1.0)
        dm = _make_dm([_alarm(no_water=True, alarm_raw=1)] + _completed_alarms())
        watcher = PumpWatcherService(
            repo,
            dm,
            poll_seconds=1.0,
            warmup_seconds=0.0,
            clock=clock,
            sleep=clock.sleep,
        )

        watcher.watch(irrigator, duration_seconds=10, started_at=300)

        activity = repo.list_activity_events(source=SOURCE_PUMP, limit=5)
        assert len(activity) == 1
        assert activity[0].code == "pump_dry_run"
        assert activity[0].severity == "critical"
        # Payload includes alarm_raw and irrigator_id
        import json

        payload = json.loads(activity[0].payload_json)
        assert payload["alarm_raw"] == 1
        assert payload["irrigator_id"] == irrigator.id
        assert payload["alarm_dp"] == 105


class TestWatcherDegradedSignals:
    def test_intermittent_read_failure_does_not_trip(self, repo, irrigator):
        """A single failed read recovers; the watcher does not stop the pump."""
        clock = FakeClock(step=1.0)
        reads = [
            _alarm(no_water=False, alarm_raw=0, error="local read failed: timeout"),
            _alarm(no_water=False, alarm_raw=0),
            _alarm(no_water=False, alarm_raw=0),
        ] + _completed_alarms()
        dm = _make_dm(reads)
        watcher = PumpWatcherService(
            repo,
            dm,
            poll_seconds=1.0,
            warmup_seconds=0.0,
            max_read_failures=3,
            clock=clock,
            sleep=clock.sleep,
        )

        result = watcher.watch(irrigator, duration_seconds=5)

        assert result["outcome"] == "completed"
        dm.irrigator_off.assert_not_called()

    def test_abandons_after_consecutive_failures(self, repo, irrigator):
        """Persistent local-read failures abandon the watch without stopping the pump."""
        clock = FakeClock(step=1.0)
        reads = [_alarm(no_water=False, error="local read failed: timeout") for _ in range(10)]
        dm = _make_dm(reads)
        watcher = PumpWatcherService(
            repo,
            dm,
            poll_seconds=1.0,
            warmup_seconds=0.0,
            max_read_failures=3,
            clock=clock,
            sleep=clock.sleep,
        )

        result = watcher.watch(irrigator, duration_seconds=60)

        assert result["outcome"] == "abandoned"
        assert result["read_failures"] == 3
        dm.irrigator_off.assert_not_called()
        events = repo.get_recent_events(irrigator.id, hours=1)
        assert all(e.action != EVENT_ACTION_ABORTED for e in events)


class TestWatcherTripSideEffectsAreRobust:
    def test_trip_proceeds_even_if_irrigator_off_raises(self, repo, irrigator):
        """If the stop call raises, the alert and event are still recorded."""
        clock = FakeClock(step=1.0)
        dm = _make_dm([_alarm(no_water=True, alarm_raw=1)] + _completed_alarms())
        dm.irrigator_off.side_effect = ConnectionError("device offline")
        watcher = PumpWatcherService(
            repo,
            dm,
            poll_seconds=1.0,
            warmup_seconds=0.0,
            clock=clock,
            sleep=clock.sleep,
            monitor=_make_monitor(repo),
        )

        result = watcher.watch(irrigator, duration_seconds=10)

        assert result["outcome"] == "tripped"
        # Alert still raised even though the physical stop failed
        open_alerts = repo.list_alerts(limit=10)
        pump_alerts = [a for a in open_alerts if a.code == ALERT_CODE]
        assert len(pump_alerts) == 1
        # Event row marks the abort attempt
        events = repo.get_recent_events(irrigator.id, hours=1)
        aborted = [e for e in events if e.action == EVENT_ACTION_ABORTED]
        assert len(aborted) == 1


class TestWatcherRoutesThroughMonitor:
    """The watcher feeds its trip observation back into DeviceHealthMonitor."""

    def test_trip_records_into_monitor_cache(self, repo, irrigator):
        """After a trip, the monitor's cache reflects NO_WATER for the irrigator."""
        from greenhouse_core.models import ENTITY_IRRIGATOR

        clock = FakeClock(step=1.0)
        dm = _make_dm([_alarm(no_water=True, alarm_raw=1)] + _completed_alarms())
        monitor = _make_monitor(repo)
        watcher = PumpWatcherService(
            repo,
            dm,
            poll_seconds=1.0,
            warmup_seconds=0.0,
            clock=clock,
            sleep=clock.sleep,
            monitor=monitor,
        )

        watcher.watch(irrigator, duration_seconds=5)

        blocked, alarms = monitor.is_actuation_blocked(irrigator)
        assert blocked is True
        assert HealthAlarm.NO_WATER in alarms
        # And the unified health: alert key is set
        from sqlalchemy import select

        from greenhouse_core.models import Alert

        key = f"health:{ENTITY_IRRIGATOR}:{irrigator.id}:no_water"
        alert = repo.session.scalar(select(Alert).where(Alert.dedup_key == key))
        assert alert is not None
        assert alert.status == "open"


class TestWatcherIrrigatorModel:
    def test_uses_irrigator_cluster_id_for_alert(self, repo):
        """The alert is filed under the irrigator's cluster, not a hardcoded id."""
        cluster_a = repo.add_cluster("A")
        cluster_b = repo.add_cluster("B")
        repo.add_irrigator(
            cluster_id=cluster_a,
            tuya_device_id="irr_a",
            name="A",
            irrigator_type="tuya_cloud",
            config={},
        )
        irrigator_b_id = repo.add_irrigator(
            cluster_id=cluster_b,
            tuya_device_id="irr_b",
            name="B",
            irrigator_type="tuya_cloud",
            config={},
        )
        repo.session.commit()
        irrigator_b: Irrigator = repo.get_irrigator(irrigator_b_id)

        clock = FakeClock(step=1.0)
        dm = _make_dm([_alarm(no_water=True, alarm_raw=1)] + _completed_alarms())
        watcher = PumpWatcherService(
            repo,
            dm,
            poll_seconds=1.0,
            warmup_seconds=0.0,
            clock=clock,
            sleep=clock.sleep,
            monitor=_make_monitor(repo),
        )

        watcher.watch(irrigator_b, duration_seconds=5)

        alerts_b = repo.list_alerts(cluster_id=cluster_b, limit=10)
        alerts_a = repo.list_alerts(cluster_id=cluster_a, limit=10)
        assert len(alerts_b) == 1
        assert alerts_b[0].code == ALERT_CODE
        assert len(alerts_a) == 0
