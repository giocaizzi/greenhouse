"""Tests for the leak / stuck-valve detection service."""

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

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


class TestLeakDetection:
    def test_pinned_moisture_raises_critical_alert(self, repo, plant_db, seeded):
        """Sensor pinned above 95% after irrigation triggers a critical alert."""
        cluster_id, sensor_id, irrigator_id = seeded
        started_at = int(time.time()) - 2000

        # Before-window: normal 50%
        for i in range(3):
            repo.add_sensor_reading(sensor_id, timestamp=started_at - 500 + i * 60, soil_moisture=50.0)

        # After-window: pinned at 99%
        for i in range(5):
            repo.add_sensor_reading(sensor_id, timestamp=started_at + 60 + i * 60, soil_moisture=99.0)

        repo.session.commit()

        svc = LeakDetectionService(repo, plant_db)
        alerts = svc.check_after_irrigation(cluster_id, started_at)

        assert len(alerts) >= 1
        alert = alerts[0]
        assert alert.severity == "critical"
        assert alert.code == "leak_or_stuck_valve"
        assert alert.source == "leak"
        assert alert.cluster_id == cluster_id

    def test_still_rising_moisture_raises_critical_alert(self, repo, plant_db, seeded):
        """Sensor still rising > 30pp after irrigation triggers a critical alert."""
        cluster_id, sensor_id, irrigator_id = seeded
        started_at = int(time.time()) - 2000

        # Before-window: low moisture ~20%
        for i in range(3):
            repo.add_sensor_reading(sensor_id, timestamp=started_at - 500 + i * 60, soil_moisture=20.0)

        # After-window: consistently high ~85% (delta > 30)
        for i in range(5):
            repo.add_sensor_reading(sensor_id, timestamp=started_at + 60 + i * 60, soil_moisture=85.0)

        repo.session.commit()

        svc = LeakDetectionService(repo, plant_db)
        alerts = svc.check_after_irrigation(cluster_id, started_at)

        assert len(alerts) >= 1
        assert alerts[0].severity == "critical"

    def test_cooldown_event_logged_on_leak(self, repo, plant_db, seeded):
        """A schedule_updated event is logged for each irrigator when leak is detected."""
        cluster_id, sensor_id, irrigator_id = seeded
        started_at = int(time.time()) - 2000

        for i in range(5):
            repo.add_sensor_reading(sensor_id, timestamp=started_at + 60 + i * 60, soil_moisture=99.0)
        repo.session.commit()

        svc = LeakDetectionService(repo, plant_db)
        alerts = svc.check_after_irrigation(cluster_id, started_at)

        assert alerts, "Expected at least one leak alert"

        events = repo.get_recent_events(irrigator_id, hours=24)
        cooldown_events = [e for e in events if e.action == "schedule_updated" and e.triggered_by == "leak_detector"]
        assert len(cooldown_events) >= 1

    def test_normal_moisture_no_alert(self, repo, plant_db, seeded):
        """Normal moisture settlement after irrigation produces no alert."""
        cluster_id, sensor_id, irrigator_id = seeded
        started_at = int(time.time()) - 2000

        # Before: dry
        for i in range(3):
            repo.add_sensor_reading(sensor_id, timestamp=started_at - 400 + i * 60, soil_moisture=30.0)

        # After: settled to 60% (not pinned, avg_after=60, avg_before=30, delta=30 — exactly at boundary)
        # Use 55% to stay just below the 30pp rising threshold
        for i in range(5):
            repo.add_sensor_reading(sensor_id, timestamp=started_at + 60 + i * 60, soil_moisture=55.0)

        repo.session.commit()

        svc = LeakDetectionService(repo, plant_db)
        alerts = svc.check_after_irrigation(cluster_id, started_at)

        assert alerts == []

    def test_no_after_readings_produces_no_alert(self, repo, plant_db, seeded):
        """When no after-window readings exist, no alert is raised."""
        cluster_id, sensor_id, irrigator_id = seeded
        started_at = int(time.time()) - 2000
        repo.session.commit()

        svc = LeakDetectionService(repo, plant_db)
        alerts = svc.check_after_irrigation(cluster_id, started_at)

        assert alerts == []

    def test_dedup_key_collapses_repeated_calls(self, repo, plant_db, seeded):
        """Repeated check_after_irrigation with same conditions bumps occurrence_count."""
        cluster_id, sensor_id, irrigator_id = seeded
        started_at = int(time.time()) - 2000

        for i in range(5):
            repo.add_sensor_reading(sensor_id, timestamp=started_at + 60 + i * 60, soil_moisture=99.0)
        repo.session.commit()

        svc = LeakDetectionService(repo, plant_db)
        svc.check_after_irrigation(cluster_id, started_at)
        repo.session.commit()
        svc.check_after_irrigation(cluster_id, started_at)
        repo.session.commit()

        all_alerts = repo.list_alerts(cluster_id=cluster_id)
        leak_alerts = [a for a in all_alerts if a.code == "leak_or_stuck_valve"]
        assert len(leak_alerts) == 1
        assert leak_alerts[0].occurrence_count == 2
