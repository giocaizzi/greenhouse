"""Tests for the leak / stuck-valve detection service.

The detector's job is to tell two similar-looking shapes apart:

- a **successful irrigation** — moisture jumps, then plateaus or drains back
  inside the observation window;
- a **leak / stuck valve** — moisture is still climbing when the window closes,
  or the probe sits pinned at the top of its range.

Because a positive verdict raises a critical alert *and* holds the cluster for
``LEAK_HOLD_HOURS``, the third possible answer — "not enough data to say" —
must stay silent. Issue #103: an empty baseline window was read as "soil was at
0%", so every healthy cycle looked like a flood.
"""

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from greenhouse_core.constants import LEAK_ALERT_CODE, LEAK_HOLD_HOURS
from greenhouse_core.database import init_db
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.leak import LeakDetectionService


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def repo(db_session):
    return IrrigationRepository(db_session)


@pytest.fixture
def plant_db():
    return PlantDatabase()


@pytest.fixture
def seeded(repo):
    """Seed cluster, sensor, irrigator; return (cluster_id, sensor_id, irrigator_id)."""
    cluster_id = repo.add_cluster("Leak Test Cluster")
    sensor_id = repo.add_sensor(cluster_id, "fake_sensor_leak", "Soil Sensor", "soil_moisture", {})
    irrigator_id = repo.add_irrigator(cluster_id, "fake_irrigator_leak", "Pump", "tuya_cloud", {})
    repo.session.commit()
    return cluster_id, sensor_id, irrigator_id


@pytest.fixture
def started_at():
    """Start timestamp far enough in the past that both windows are closed."""
    return int(time.time()) - 2000


def _seed(repo, sensor_id, started_at, *, before=(), after=()):
    """Write a baseline and an after-window series around ``started_at``.

    ``before`` samples are spread backwards on a 30-minute grid (the baseline
    window is hours wide); ``after`` samples land inside the 30-minute
    observation window, oldest first.
    """
    for i, value in enumerate(reversed(before)):
        repo.add_sensor_reading(sensor_id, timestamp=started_at - 600 - i * 1800, soil_moisture=value)
    for i, value in enumerate(after):
        repo.add_sensor_reading(sensor_id, timestamp=started_at + 300 + i * 500, soil_moisture=value)
    repo.session.commit()


def _leak_alerts(repo, cluster_id):
    return [a for a in repo.list_alerts(cluster_id=cluster_id) if a.code == LEAK_ALERT_CODE]


class TestInsufficientData:
    """No data, thin data, or no baseline → silence. Never a hold."""

    def test_no_readings_at_all_produces_no_alert(self, repo, plant_db, seeded, started_at):
        cluster_id, _sensor_id, _irrigator_id = seeded

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert alerts == []

    def test_empty_baseline_window_does_not_alert_on_normal_moisture(self, repo, plant_db, seeded, started_at):
        """Regression for #103 — the bug that fired on every single cycle.

        Sensor rows land in SQLite at sync time (default every 3h), so the
        window before a start event is routinely empty. The old rule filled
        that gap with ``avg_before = 0.0``, which turned "still rising" into
        "moisture > 30%" — true of any healthy pot.
        """
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(), after=(38.0, 40.0, 39.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert alerts == []
        assert _leak_alerts(repo, cluster_id) == []

    def test_too_few_after_samples_produces_no_alert(self, repo, plant_db, seeded, started_at):
        """Two post-irrigation samples cannot show a shape — stay silent."""
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 38.0), after=(70.0, 80.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert alerts == []

    def test_missing_baseline_does_not_release_an_existing_hold(self, repo, plant_db, seeded, started_at):
        """An inconclusive check must not resolve a hold an earlier check raised."""
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(), after=(97.0, 98.0, 99.0))
        svc = LeakDetectionService(repo, plant_db)
        assert svc.check_after_irrigation(cluster_id, started_at) != []
        repo.session.commit()

        # A later cycle with no baseline at all: cannot judge, must not clear.
        later = started_at + 30_000
        _seed(repo, sensor_id, later, before=(), after=(50.0, 51.0, 50.0))
        svc.check_after_irrigation(cluster_id, later)
        repo.session.commit()

        assert [a.status for a in _leak_alerts(repo, cluster_id)] == ["open"]


class TestSuccessfulIrrigation:
    """A dose that lands must never be reported as a leak."""

    def test_jump_then_plateau_produces_no_alert(self, repo, plant_db, seeded, started_at):
        """38% → 66% in one burst, then flat: the irrigation worked."""
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 37.0), after=(66.0, 66.0, 65.0, 66.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert alerts == []

    def test_jump_then_drain_back_produces_no_alert(self, repo, plant_db, seeded, started_at):
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(35.0, 36.0, 35.0), after=(72.0, 68.0, 64.0, 61.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert alerts == []

    def test_slow_soak_below_the_rise_threshold_produces_no_alert(self, repo, plant_db, seeded, started_at):
        """Still climbing, but nowhere near a flood's worth of water."""
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(40.0, 40.0, 41.0), after=(44.0, 48.0, 52.0, 55.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert alerts == []


class TestLeakDetected:
    """The two shapes that are genuinely wrong."""

    def test_never_settles_raises_critical_alert(self, repo, plant_db, seeded, started_at):
        """Moisture still climbing when the window closes, 30pp+ over baseline."""
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 37.0), after=(50.0, 65.0, 78.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.severity == "critical"
        assert alert.code == LEAK_ALERT_CODE
        assert alert.source == "leak"
        assert alert.cluster_id == cluster_id
        assert "still rising" in alert.message

    def test_pinned_high_raises_alert_without_a_baseline(self, repo, plant_db, seeded, started_at):
        """A probe stuck above 95% is wrong regardless of where it started."""
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(), after=(97.0, 98.0, 99.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert len(alerts) == 1
        assert "pinned" in alerts[0].message

    def test_single_pinned_sample_is_not_enough(self, repo, plant_db, seeded, started_at):
        """One reading at the top of the range is a glitch, not a flood."""
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(50.0, 50.0, 50.0), after=(52.0, 51.0, 99.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert alerts == []

    def test_alert_payload_carries_the_evidence(self, repo, plant_db, seeded, started_at):
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 37.0), after=(50.0, 65.0, 78.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        import json

        payload = json.loads(alerts[0].payload_json)
        assert payload["sensor_id"] == sensor_id
        assert payload["hold_hours"] == LEAK_HOLD_HOURS
        assert payload["baseline"] == pytest.approx(38.0)
        assert payload["latest_moisture"] == pytest.approx(78.0)
        assert payload["rise_over_baseline"] == pytest.approx(40.0)


class TestCleaningIsApplied:
    """The detector reads the same cleaned view as the decision engine."""

    def test_outlier_spike_does_not_raise_an_alert(self, repo, plant_db, seeded, started_at):
        """The exact #103 signature: one +4σ sample the anomaly scan flags as drift.

        Raw, the after-window averages ~45% against an empty-ish baseline and
        the old rule cried leak. Through the Hampel filter the spike drops out
        and the remaining series is plainly settled.
        """
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 38.0), after=(38.0, 66.0, 38.0, 39.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert alerts == []

    def test_sustained_rise_still_alerts(self, repo, plant_db, seeded, started_at):
        """Counterpart to the spike case: a real climb survives cleaning."""
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 38.0), after=(52.0, 63.0, 71.0, 79.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert len(alerts) == 1


class TestHoldLifecycle:
    """What a finding does beyond the alert row."""

    def test_activity_event_records_the_hold(self, repo, plant_db, seeded, started_at):
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 37.0), after=(50.0, 65.0, 78.0))

        LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)
        repo.session.commit()

        events = repo.list_activity_events(entity_type="cluster", entity_id=cluster_id)
        hold_events = [e for e in events if e.code == "leak_hold"]
        assert len(hold_events) == 1
        assert hold_events[0].severity == "critical"
        assert f"{LEAK_HOLD_HOURS}h" in hold_events[0].message

    def test_no_fake_irrigation_event_is_written(self, repo, plant_db, seeded, started_at):
        """The hold is real now — it must not be faked as a schedule_updated row.

        The old implementation logged ``schedule_updated`` claiming a 24h
        auto-cancel, but ``_enforce_cooldown`` only ever counted ``start``
        events, so that row blocked nothing.
        """
        cluster_id, sensor_id, irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 37.0), after=(50.0, 65.0, 78.0))

        LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)
        repo.session.commit()

        assert repo.get_recent_events(irrigator_id, hours=24) == []

    def test_settled_sensor_resolves_its_open_alert(self, repo, plant_db, seeded, started_at):
        """A later well-behaved cycle releases the hold automatically."""
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 37.0), after=(50.0, 65.0, 78.0))
        svc = LeakDetectionService(repo, plant_db)
        svc.check_after_irrigation(cluster_id, started_at)
        repo.session.commit()

        later = started_at + 30_000
        _seed(repo, sensor_id, later, before=(40.0, 40.0, 41.0), after=(62.0, 61.0, 60.0))
        svc.check_after_irrigation(cluster_id, later)
        repo.session.commit()

        assert [a.status for a in _leak_alerts(repo, cluster_id)] == ["resolved"]

    def test_clearing_touches_only_this_sensors_leak_alert(self, repo, plant_db, seeded, started_at):
        """A settled sensor resolves its own leak alert — not the neighbour's, not other codes."""
        cluster_id, sensor_id, _irrigator_id = seeded
        other_id = repo.add_sensor(cluster_id, "fake_sensor_leak_3", "Neighbour Sensor", "soil_moisture", {})
        repo.upsert_alert(
            dedup_key=f"leak::{LEAK_ALERT_CODE}::{cluster_id}::sensor{other_id}",
            source="leak",
            code=LEAK_ALERT_CODE,
            title="Possible leak or stuck valve detected",
            message="Neighbour Sensor: soil moisture pinned >95% (latest=99.0%)",
            severity="critical",
            entity_type="sensor",
            entity_id=other_id,
            cluster_id=cluster_id,
        )
        repo.upsert_alert(
            dedup_key=f"anomaly::sensor_drift::{cluster_id}::sensor{sensor_id}",
            source="anomaly",
            code="sensor_drift",
            title="Sensor drift",
            message="Soil Sensor: drifting",
            severity="warning",
            entity_type="sensor",
            entity_id=sensor_id,
            cluster_id=cluster_id,
        )
        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 37.0), after=(50.0, 65.0, 78.0))
        svc = LeakDetectionService(repo, plant_db)
        svc.check_after_irrigation(cluster_id, started_at)
        repo.session.commit()

        later = started_at + 30_000
        _seed(repo, sensor_id, later, before=(40.0, 40.0, 41.0), after=(62.0, 61.0, 60.0))
        svc.check_after_irrigation(cluster_id, later)
        repo.session.commit()

        by_entity = {(a.code, a.entity_id): a.status for a in repo.list_alerts(cluster_id=cluster_id)}
        assert by_entity[(LEAK_ALERT_CODE, sensor_id)] == "resolved"
        assert by_entity[(LEAK_ALERT_CODE, other_id)] == "open"
        assert by_entity[("sensor_drift", sensor_id)] == "open"

    def test_dedup_key_collapses_repeated_calls(self, repo, plant_db, seeded, started_at):
        cluster_id, sensor_id, _irrigator_id = seeded
        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 37.0), after=(50.0, 65.0, 78.0))

        svc = LeakDetectionService(repo, plant_db)
        svc.check_after_irrigation(cluster_id, started_at)
        repo.session.commit()
        svc.check_after_irrigation(cluster_id, started_at)
        repo.session.commit()

        alerts = _leak_alerts(repo, cluster_id)
        assert len(alerts) == 1
        assert alerts[0].occurrence_count == 2


class TestPerSensorScoping:
    def test_only_the_offending_sensor_is_alerted(self, repo, plant_db, seeded, started_at):
        cluster_id, sensor_id, _irrigator_id = seeded
        other_id = repo.add_sensor(cluster_id, "fake_sensor_leak_2", "Second Sensor", "soil_moisture", {})
        repo.session.commit()

        _seed(repo, sensor_id, started_at, before=(38.0, 38.0, 37.0), after=(50.0, 65.0, 78.0))
        _seed(repo, other_id, started_at, before=(40.0, 40.0, 41.0), after=(62.0, 61.0, 60.0))

        alerts = LeakDetectionService(repo, plant_db).check_after_irrigation(cluster_id, started_at)

        assert len(alerts) == 1
        assert alerts[0].entity_id == sensor_id
