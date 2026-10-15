"""Tests for the sensor anomaly detection service (stale, drift, flatline)."""

import time
from collections.abc import Iterator

import pytest
import time_machine
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from greenhouse_core.constants import SENSOR_FLATLINE_HOURS, SENSOR_OFFLINE_GRACE_MINUTES
from greenhouse_core.database import init_db
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.anomaly import SensorAnomalyService


@pytest.fixture
def db_session() -> Iterator[Session]:
    """A session on a fresh, Alembic-migrated in-memory database."""
    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def repo(db_session: Session) -> IrrigationRepository:
    """A repository on the test's migrated session."""
    return IrrigationRepository(db_session)


@pytest.fixture
def cluster_and_sensor(repo: IrrigationRepository) -> tuple[int, int]:
    """Seed a cluster and sensor; return (cluster_id, sensor_id)."""
    cluster_id = repo.add_cluster("Anomaly Test Cluster")
    sensor_id = repo.add_sensor(cluster_id, "fake_anomaly_sensor", "Test Sensor", "tuya.tr301z", {})
    repo.session.commit()
    return cluster_id, sensor_id


class TestSensorStale:
    """Tests for sensor stale."""

    def _seed_regular(
        self, repo: IrrigationRepository, sensor_id: int, *, count: int, interval_s: int, end_offset_s: int = 0
    ) -> None:
        """Seed ``count`` readings spaced ``interval_s`` apart, ending ``end_offset_s`` seconds ago."""
        now = int(time.time())
        base = now - end_offset_s - (count - 1) * interval_s
        for i in range(count):
            repo.add_sensor_reading(sensor_id, timestamp=base + i * interval_s, soil_moisture=50.0)
        repo.session.commit()

    def test_stale_sensor_detected(self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]) -> None:
        """A 90-minute silence after 5-minute intervals, past a 30-min sync + 30-min grace, triggers sensor_stale."""
        cluster_id, sensor_id = cluster_and_sensor
        # 12 readings every 5 min; last reading 90 min ago
        # gap = 5400s > max(2x300s, (30 + 30) min = 3600s)
        self._seed_regular(repo, sensor_id, count=12, interval_s=300, end_offset_s=5400)

        alerts = SensorAnomalyService(repo, sync_interval_minutes=30).scan()

        stale = [a for a in alerts if a.code == "sensor_stale"]
        assert len(stale) >= 1
        assert stale[0].severity == "warning"
        assert stale[0].source == "anomaly"
        assert stale[0].cluster_id == cluster_id

    def test_fresh_sensor_no_stale_alert(self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]) -> None:
        """A sensor that just reported is not stale."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_regular(repo, sensor_id, count=12, interval_s=300, end_offset_s=0)

        alerts = SensorAnomalyService(repo).scan()

        assert [a for a in alerts if a.code == "sensor_stale"] == []

    def test_silence_between_syncs_is_not_stale(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """29 min without a reading, 10-min median gap, 30-min sync: within sync + grace → no alert."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_regular(repo, sensor_id, count=12, interval_s=600, end_offset_s=29 * 60)

        assert SensorAnomalyService(repo, sync_interval_minutes=30).scan() == []

    def test_silence_past_sync_and_grace_is_stale(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """Silent one minute past sync interval + SENSOR_OFFLINE_GRACE_MINUTES → sensor_stale."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_regular(
            repo, sensor_id, count=12, interval_s=600, end_offset_s=(30 + SENSOR_OFFLINE_GRACE_MINUTES + 1) * 60
        )

        alerts = SensorAnomalyService(repo, sync_interval_minutes=30).scan()

        assert [a.code for a in alerts] == ["sensor_stale"]

    def test_stale_alert_resolves_once_fresh(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """An open sensor_stale alert resolves on the first scan that finds the sensor fresh."""
        cluster_id, sensor_id = cluster_and_sensor
        self._seed_regular(repo, sensor_id, count=12, interval_s=600, end_offset_s=2 * 3600)
        svc = SensorAnomalyService(repo, sync_interval_minutes=30)
        (alert,) = svc.scan()
        repo.session.commit()

        repo.add_sensor_reading(sensor_id, timestamp=int(time.time()), soil_moisture=50.0)
        repo.session.commit()
        assert svc.scan() == []
        repo.session.commit()

        (row,) = [a for a in repo.list_alerts(cluster_id=cluster_id) if a.code == "sensor_stale"]
        assert (row.id, row.status) == (alert.id, "resolved")

    def test_too_few_readings_skipped(self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]) -> None:
        """Sensors with fewer than 10 readings are skipped."""
        _cluster_id, sensor_id = cluster_and_sensor
        now = int(time.time())
        for i in range(5):
            repo.add_sensor_reading(sensor_id, timestamp=now - 5000 + i * 300, soil_moisture=50.0)
        repo.session.commit()

        assert SensorAnomalyService(repo).scan() == []


class TestSensorDrift:
    """Tests for sensor drift."""

    def _seed_baseline(
        self, repo: IrrigationRepository, sensor_id: int, *, count: int, moisture: float, interval_s: int = 300
    ) -> None:
        """Seed ``count`` readings at a stable moisture level."""
        now = int(time.time())
        for i in range(count):
            repo.add_sensor_reading(sensor_id, timestamp=now - (count - i) * interval_s, soil_moisture=moisture)

    def test_spike_detected(self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]) -> None:
        """A 95% reading against a ~50% baseline triggers sensor_drift (z >> 4)."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=49, moisture=50.0)
        repo.add_sensor_reading(sensor_id, timestamp=int(time.time()), soil_moisture=95.0)
        repo.session.commit()

        alerts = SensorAnomalyService(repo).scan()

        drift = [a for a in alerts if a.code == "sensor_drift"]
        assert len(drift) >= 1
        assert drift[0].severity == "warning"
        assert drift[0].source == "anomaly"

    def test_normal_reading_no_drift_alert(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """A reading within normal range of the baseline does not trigger drift."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=49, moisture=50.0)
        repo.add_sensor_reading(sensor_id, timestamp=int(time.time()), soil_moisture=52.0)
        repo.session.commit()

        drift = [a for a in SensorAnomalyService(repo).scan() if a.code == "sensor_drift"]
        assert drift == []

    def test_constant_series_no_drift_alert(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """A perfectly constant series (std=0) does not trigger a drift alert."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=50, moisture=50.0)
        repo.session.commit()

        drift = [a for a in SensorAnomalyService(repo).scan() if a.code == "sensor_drift"]
        assert drift == []

    def test_dedup_collapses_repeat_scans(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
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
        scans = 2  # scan() ran twice on the same spike above
        assert drift_alerts[0].occurrence_count >= scans

    def _seed_tail(self, repo: IrrigationRepository, sensor_id: int, values: tuple[float, ...]) -> None:
        """Append ``values`` (oldest first) as the newest readings, one second apart, ending now."""
        now = int(time.time())
        for i, moisture in enumerate(values):
            repo.add_sensor_reading(sensor_id, timestamp=now - len(values) + 1 + i, soil_moisture=moisture)
        repo.session.commit()

    def test_watering_step_that_holds_is_not_drift(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """#144: 1 % for days, then a hand watering to ~40 % that holds → no sensor_drift."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=47, moisture=1.0)
        self._seed_tail(repo, sensor_id, (40.0, 40.0, 39.0))

        assert [a for a in SensorAnomalyService(repo).scan() if a.code == "sensor_drift"] == []

    def test_open_drift_resolves_once_the_step_holds(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """The first post-watering reading is flagged; once the new level holds the alert resolves."""
        cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=49, moisture=1.0, interval_s=600)
        self._seed_tail(repo, sensor_id, (40.0,))
        svc = SensorAnomalyService(repo)
        (alert,) = [a for a in svc.scan() if a.code == "sensor_drift"]
        repo.session.commit()

        with time_machine.travel(time.time() + 60, tick=False):
            self._seed_tail(repo, sensor_id, (40.0, 39.0))
            assert [a for a in svc.scan() if a.code == "sensor_drift"] == []
        repo.session.commit()

        (row,) = [a for a in repo.list_alerts(cluster_id=cluster_id) if a.code == "sensor_drift"]
        assert (row.id, row.status) == (alert.id, "resolved")

    def test_open_drift_resolves_once_the_latest_reading_is_back_in_range(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """A spike that passes: the next scan whose latest reading has |z| <= 4 resolves the alert."""
        cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=49, moisture=50.0, interval_s=600)
        self._seed_tail(repo, sensor_id, (95.0,))
        svc = SensorAnomalyService(repo)
        (alert,) = [a for a in svc.scan() if a.code == "sensor_drift"]
        repo.session.commit()

        with time_machine.travel(time.time() + 60, tick=False):
            self._seed_tail(repo, sensor_id, (51.0,))
            assert [a for a in svc.scan() if a.code == "sensor_drift"] == []
        repo.session.commit()

        (row,) = [a for a in repo.list_alerts(cluster_id=cluster_id) if a.code == "sensor_drift"]
        assert (row.id, row.status) == (alert.id, "resolved")

    def test_falling_step_is_still_drift(self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]) -> None:
        """Only a rising step reads as a watering: a sudden drop that holds stays sensor_drift."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=47, moisture=50.0)
        self._seed_tail(repo, sensor_id, (5.0, 5.0, 5.0))

        assert [a.code for a in SensorAnomalyService(repo).scan()] == ["sensor_drift"]

    def test_two_high_readings_are_not_yet_a_step(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """Fewer than ANOMALY_STEP_HOLD_READINGS high values are still judged as a spike."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_baseline(repo, sensor_id, count=48, moisture=50.0)
        self._seed_tail(repo, sensor_id, (95.0, 95.0))

        assert [a.code for a in SensorAnomalyService(repo).scan()] == ["sensor_drift"]


class TestSensorFlatline:
    """#147: a fresh soil sensor flat at 0 % / 100 % for SENSOR_FLATLINE_HOURS."""

    def _seed_series(
        self, repo: IrrigationRepository, sensor_id: int, values: list[float], *, end_offset_s: int = 0
    ) -> None:
        """Seed ``values`` (oldest first) every 30 min, the newest ``end_offset_s`` seconds ago."""
        newest = int(time.time()) - end_offset_s
        for i, moisture in enumerate(values):
            repo.add_sensor_reading(sensor_id, timestamp=newest - (len(values) - 1 - i) * 1800, soil_moisture=moisture)
        repo.session.commit()

    @staticmethod
    def _flat(hours: int, level: float) -> list[float]:
        """A flat series covering ``hours`` at 30-min spacing (inclusive of both ends)."""
        return [level] * (2 * hours + 1)

    @pytest.mark.parametrize("level", [0.0, 100.0])
    def test_flat_at_an_extreme_with_fresh_readings(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int], level: float
    ) -> None:
        """48 h of 0 % or 100 % with fresh timestamps → one sensor_flatline warning."""
        cluster_id, sensor_id = cluster_and_sensor
        self._seed_series(repo, sensor_id, self._flat(SENSOR_FLATLINE_HOURS, level))

        (alert,) = SensorAnomalyService(repo).scan()

        assert (alert.code, alert.severity, alert.cluster_id) == ("sensor_flatline", "warning", cluster_id)
        assert alert.message == (
            f"Test Sensor: soil moisture flat at {level:.1f}% for 48 h while readings keep arriving "
            "(probe out of the soil, in water, or faulty?)"
        )

    def test_saturated_within_one_point_still_flat(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """A 99-100 % series (the live Dracaena case) is flat within SENSOR_FLATLINE_MAX_RANGE."""
        _cluster_id, sensor_id = cluster_and_sensor
        series = [99.0 + (i % 2) for i in range(2 * SENSOR_FLATLINE_HOURS + 1)]
        self._seed_series(repo, sensor_id, series)

        assert [a.code for a in SensorAnomalyService(repo).scan()] == ["sensor_flatline"]

    def test_watering_step_ends_the_flatline(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """The same 0 % series with a held watering step raises nothing (no flatline, no drift)."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_series(repo, sensor_id, [*self._flat(SENSOR_FLATLINE_HOURS, 0.0), 40.0, 40.0, 39.0])

        assert SensorAnomalyService(repo).scan() == []

    def test_open_flatline_resolves_once_the_series_moves(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]
    ) -> None:
        """An open sensor_flatline alert resolves on the first scan that sees the series move."""
        cluster_id, sensor_id = cluster_and_sensor
        self._seed_series(repo, sensor_id, self._flat(SENSOR_FLATLINE_HOURS, 0.0), end_offset_s=3 * 1800)
        svc = SensorAnomalyService(repo)
        (alert,) = svc.scan()
        repo.session.commit()

        self._seed_series(repo, sensor_id, [40.0, 40.0, 39.0])
        assert svc.scan() == []
        repo.session.commit()

        (row,) = [a for a in repo.list_alerts(cluster_id=cluster_id) if a.code == "sensor_flatline"]
        assert (row.id, row.status) == (alert.id, "resolved")

    def test_stale_sensor_is_only_stale(self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int]) -> None:
        """A flat series that stopped arriving is sensor_stale, not sensor_flatline."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_series(repo, sensor_id, self._flat(SENSOR_FLATLINE_HOURS, 0.0), end_offset_s=6 * 3600)

        assert [a.code for a in SensorAnomalyService(repo).scan()] == ["sensor_stale"]

    @pytest.mark.parametrize(
        ("hours", "level"),
        [(SENSOR_FLATLINE_HOURS - 1, 0.0), (SENSOR_FLATLINE_HOURS, 50.0), (SENSOR_FLATLINE_HOURS, 3.0)],
        ids=["too-short", "mid-range", "just-above-dry-extreme"],
    )
    def test_no_flatline(
        self, repo: IrrigationRepository, cluster_and_sensor: tuple[int, int], hours: int, level: float
    ) -> None:
        """Shorter than SENSOR_FLATLINE_HOURS, or flat away from an extreme, is not a flatline."""
        _cluster_id, sensor_id = cluster_and_sensor
        self._seed_series(repo, sensor_id, self._flat(hours, level))

        assert SensorAnomalyService(repo).scan() == []
