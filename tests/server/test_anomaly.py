"""Tests for the sensor anomaly detection service (stale + drift)."""

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from greenhouse_core.database import init_db
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.anomaly import SensorAnomalyService


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
def cluster_and_sensor(repo):
    """Seed a cluster and sensor; return (cluster_id, sensor_id)."""
    cluster_id = repo.add_cluster("Anomaly Test Cluster")
    sensor_id = repo.add_sensor(cluster_id, "fake_anomaly_sensor", "Test Sensor", "soil_moisture", {})
    repo.session.commit()
    return cluster_id, sensor_id


class TestSensorStale:
    def _seed_regular(self, repo, sensor_id, *, count: int, interval_s: int, end_offset_s: int = 0):
        """Seed ``count`` readings spaced ``interval_s`` apart, ending ``end_offset_s`` seconds ago."""
        now = int(time.time())
        base = now - end_offset_s - (count - 1) * interval_s
        for i in range(count):
            repo.add_sensor_reading(sensor_id, timestamp=base + i * interval_s, soil_moisture=50.0)
        repo.session.commit()

    def test_stale_sensor_detected(self, repo, cluster_and_sensor):
        """A 90-minute silence after 5-minute intervals triggers sensor_stale."""
        cluster_id, sensor_id = cluster_and_sensor
        # 12 readings every 5 min; last reading 90 min ago
        # median interval = 300s; gap = 5400s > 2×300 = 600s
        self._seed_regular(repo, sensor_id, count=12, interval_s=300, end_offset_s=5400)

        alerts = SensorAnomalyService(repo).scan()

        stale = [a for a in alerts if a.code == "sensor_stale"]
        assert len(stale) >= 1
        assert stale[0].severity == "warning"
        assert stale[0].source == "anomaly"
        assert stale[0].cluster_id == cluster_id

    def test_fresh_sensor_no_stale_alert(self, repo, cluster_and_sensor):
        """A sensor that just reported is not stale."""
        cluster_id, sensor_id = cluster_and_sensor
        self._seed_regular(repo, sensor_id, count=12, interval_s=300, end_offset_s=0)

        alerts = SensorAnomalyService(repo).scan()

        assert [a for a in alerts if a.code == "sensor_stale"] == []

    def test_too_few_readings_skipped(self, repo, cluster_and_sensor):
        """Sensors with fewer than 10 readings are skipped."""
        cluster_id, sensor_id = cluster_and_sensor
        now = int(time.time())
        for i in range(5):
            repo.add_sensor_reading(sensor_id, timestamp=now - 5000 + i * 300, soil_moisture=50.0)
        repo.session.commit()

        assert SensorAnomalyService(repo).scan() == []


class TestSensorDrift:
    def _seed_baseline(self, repo, sensor_id, *, count: int, moisture: float, interval_s: int = 300):
        """Seed ``count`` readings at a stable moisture level."""
        now = int(time.time())
        for i in range(count):
            repo.add_sensor_reading(sensor_id, timestamp=now - (count - i) * interval_s, soil_moisture=moisture)

    def test_spike_detected(self, repo, cluster_and_sensor):
        """A 95% reading against a ~50% baseline triggers sensor_drift (z >> 4)."""
        cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=49, moisture=50.0)
        repo.add_sensor_reading(sensor_id, timestamp=int(time.time()), soil_moisture=95.0)
        repo.session.commit()

        alerts = SensorAnomalyService(repo).scan()

        drift = [a for a in alerts if a.code == "sensor_drift"]
        assert len(drift) >= 1
        assert drift[0].severity == "warning"
        assert drift[0].source == "anomaly"

    def test_normal_reading_no_drift_alert(self, repo, cluster_and_sensor):
        """A reading within normal range of the baseline does not trigger drift."""
        cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=49, moisture=50.0)
        repo.add_sensor_reading(sensor_id, timestamp=int(time.time()), soil_moisture=52.0)
        repo.session.commit()

        drift = [a for a in SensorAnomalyService(repo).scan() if a.code == "sensor_drift"]
        assert drift == []

    def test_constant_series_no_drift_alert(self, repo, cluster_and_sensor):
        """A perfectly constant series (std=0) does not trigger a drift alert."""
        cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=50, moisture=50.0)
        repo.session.commit()

        drift = [a for a in SensorAnomalyService(repo).scan() if a.code == "sensor_drift"]
        assert drift == []

    def test_dedup_collapses_repeat_scans(self, repo, cluster_and_sensor):
        """Running scan twice on the same spike increments occurrence_count."""
        cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=49, moisture=50.0)
        repo.add_sensor_reading(sensor_id, timestamp=int(time.time()), soil_moisture=95.0)
        repo.session.commit()

        svc = SensorAnomalyService(repo)
        svc.scan()
        repo.session.commit()

        # Second spike reading so the latest is still anomalous
        repo.add_sensor_reading(sensor_id, timestamp=int(time.time()) + 1, soil_moisture=95.0)
        repo.session.commit()
        svc.scan()
        repo.session.commit()

        drift_alerts = [a for a in repo.list_alerts(cluster_id=cluster_id) if a.code == "sensor_drift"]
        assert len(drift_alerts) == 1
        assert drift_alerts[0].occurrence_count >= 2
