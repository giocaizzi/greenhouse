"""Tests for GET /api/v1/clusters/{cluster_id}/efficacy."""

import time
from http import HTTPStatus

from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from greenhouse_core.models import IrrigationEvent, Plant, Sensor, SensorAssignment, SensorReading
from greenhouse_server.app import create_app
from greenhouse_server.config import Settings
from greenhouse_server.deps import get_device_gateway, get_device_registry

DEFAULT_EFFICACY_DAYS = 14  # the route's ``days`` default
MAX_EFFICACY_SCORE = 100.0  # scores are capped here
# The dud event below sits at ~30 % before and after; counting its 95 % spike would push both far past these.
SPIKE_FREE_AFTER_PCT_CEILING = 35.0
SPIKE_FREE_SCORE_CEILING = 25.0


def _make_client() -> tuple[TestClient, Engine]:
    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    settings = Settings(db_url="sqlite://", enable_scheduler=False, auth_enabled=False)
    app = create_app(settings, engine=engine)
    app.dependency_overrides[get_device_registry] = lambda: None
    app.dependency_overrides[get_device_gateway] = lambda: None
    return TestClient(app, raise_server_exceptions=False), engine


def _seed_cluster_with_devices(client: TestClient) -> None:
    """Cluster 1 with one TR301Z sensor and one IK10PW irrigator ("Eff Pump"), created through the API."""
    client.post("/api/v1/clusters", json={"name": "Efficacy Cluster"})
    client.post(
        "/api/v1/clusters/1/sensors",
        json={"tuya_device_id": "fake_eff_sensor", "name": "Eff Sensor", "type": "tuya.tr301z"},
    )
    client.post(
        "/api/v1/clusters/1/irrigator",
        json={"tuya_device_id": "fake_eff_irrigator", "name": "Eff Pump", "type": "rainpoint.ik10pw"},
    )


def _seed_ten_point_rise(engine: Engine, event_ts: int, duration: int) -> None:
    """Soil at 30 % before an automatic start at ``event_ts``, 40-41 % after it."""
    with Session(engine) as session:
        sensor = session.get(Sensor, 1)
        assert sensor is not None
        irrigator_id = 1

        session.add(SensorReading(sensor_id=sensor.id, timestamp=event_ts - 900, soil_moisture=30.0))
        session.add(SensorReading(sensor_id=sensor.id, timestamp=event_ts - 300, soil_moisture=30.0))
        session.add(SensorReading(sensor_id=sensor.id, timestamp=event_ts + 1200, soil_moisture=40.0))
        session.add(SensorReading(sensor_id=sensor.id, timestamp=event_ts + 2400, soil_moisture=41.0))

        session.add(
            IrrigationEvent(
                irrigator_id=irrigator_id,
                timestamp=event_ts,
                action="start",
                duration_minutes=duration,
                triggered_by="auto",
            )
        )
        session.commit()


class TestClusterEfficacy:
    """Tests for cluster efficacy."""

    def test_not_found(self, client: TestClient) -> None:
        """Not found."""
        resp = client.get("/api/v1/clusters/999/efficacy")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_empty_cluster(self, seeded_client: TestClient) -> None:
        """Empty cluster."""
        resp = seeded_client.get("/api/v1/clusters/1/efficacy")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["cluster_id"] == 1
        assert data["days"] == DEFAULT_EFFICACY_DAYS
        assert data["items"] == []

    def test_scored_event_with_moisture_rise(self) -> None:
        """10% moisture rise after an irrigation event → positive score."""
        client, engine = _make_client()
        try:
            _seed_cluster_with_devices(client)

            now = int(time.time())
            event_ts = now - 3600

            duration = 3
            _seed_ten_point_rise(engine, event_ts, duration)

            resp = client.get("/api/v1/clusters/1/efficacy?days=1")
            assert resp.status_code == HTTPStatus.OK
            data = resp.json()
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert item["irrigator_name"] == "Eff Pump"
            assert item["duration_minutes"] == duration
            assert item["before_pct"] is not None
            assert item["after_pct"] is not None
            assert item["score"] is not None
            assert item["score"] > 0

        finally:
            engine.dispose()

    def test_spike_reading_does_not_inflate_the_score(self) -> None:
        """Efficacy is rise x 5, so one glitch sample is worth 100 phantom points.

        The event below is a dud — moisture sat at 30% before and after — with a
        single spurious 95% sample in the after-window. Read through the cleaned
        view that spike is dropped and the score stays at the floor.
        """
        client, engine = _make_client()
        try:
            _seed_cluster_with_devices(client)

            now = int(time.time())
            event_ts = now - 3600

            with Session(engine) as session:
                for offset in (-1500, -900, -300):
                    session.add(SensorReading(sensor_id=1, timestamp=event_ts + offset, soil_moisture=30.0))
                for offset, moisture in ((300, 30.0), (900, 95.0), (1500, 31.0), (2100, 30.0)):
                    session.add(SensorReading(sensor_id=1, timestamp=event_ts + offset, soil_moisture=moisture))
                session.add(
                    IrrigationEvent(
                        irrigator_id=1,
                        timestamp=event_ts,
                        action="start",
                        duration_minutes=2,
                        triggered_by="auto",
                    )
                )
                session.commit()

            resp = client.get("/api/v1/clusters/1/efficacy")
            item = resp.json()["items"][0]

            assert item["after_pct"] < SPIKE_FREE_AFTER_PCT_CEILING
            assert item["score"] < SPIKE_FREE_SCORE_CEILING
        finally:
            client.close()
            engine.dispose()

    def test_score_formula(self) -> None:
        """Exactly 20% rise should yield score 100."""
        client, engine = _make_client()
        try:
            client.post("/api/v1/clusters", json={"name": "Score Cluster"})
            client.post(
                "/api/v1/clusters/1/sensors",
                json={"tuya_device_id": "fake_score_sensor", "name": "S Sensor", "type": "tuya.tr301z"},
            )
            client.post(
                "/api/v1/clusters/1/irrigator",
                json={"tuya_device_id": "fake_score_pump", "name": "S Pump", "type": "rainpoint.ik10pw"},
            )

            now = int(time.time())
            event_ts = now - 3600

            with Session(engine) as session:
                session.add(SensorReading(sensor_id=1, timestamp=event_ts - 600, soil_moisture=50.0))
                session.add(SensorReading(sensor_id=1, timestamp=event_ts + 1800, soil_moisture=70.0))
                session.add(
                    IrrigationEvent(
                        irrigator_id=1, timestamp=event_ts, action="start", duration_minutes=5, triggered_by="manual"
                    )
                )
                session.commit()

            resp = client.get("/api/v1/clusters/1/efficacy?days=1")
            data = resp.json()
            assert len(data["items"]) == 1
            assert data["items"][0]["score"] == MAX_EFFICACY_SCORE
        finally:
            engine.dispose()

    def test_days_query_param(self, seeded_client: TestClient) -> None:
        """Days query param."""
        days = 30
        resp = seeded_client.get(f"/api/v1/clusters/1/efficacy?days={days}")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["days"] == days


def _seed_moved_sensor(engine: Engine, *, plant_cluster_before: int, move_ts: int) -> None:
    """Sensor 1 measured a plant in ``plant_cluster_before`` until ``move_ts``, then a cluster-1 plant."""
    with Session(engine) as session:
        before = Plant(cluster_id=plant_cluster_before, species="Ficus")
        after = Plant(cluster_id=1, species="Monstera")
        session.add_all([before, after])
        session.flush()
        session.add(SensorAssignment(sensor_id=1, plant_id=before.id, started_at=0, ended_at=move_ts))
        session.add(SensorAssignment(sensor_id=1, plant_id=after.id, started_at=move_ts, ended_at=None))
        sensor = session.get(Sensor, 1)
        assert sensor is not None
        sensor.plant_id = after.id
        session.commit()


class TestAssignmentAttribution:
    """Issue #185: readings count for an event only through the assignment window holding it."""

    def test_readings_from_another_clusters_pot_do_not_score(self) -> None:
        """A sensor that measured another cluster's plant at the event says nothing about this pump."""
        client, engine = _make_client()
        try:
            _seed_cluster_with_devices(client)
            client.post("/api/v1/clusters", json={"name": "Other Cluster"})
            now = int(time.time())
            event_ts = now - 3 * 3600
            _seed_ten_point_rise(engine, event_ts, 3)
            _seed_moved_sensor(engine, plant_cluster_before=2, move_ts=now - 1800)

            item = client.get("/api/v1/clusters/1/efficacy?days=1").json()["items"][0]
            assert item["before_pct"] is None
            assert item["after_pct"] is None
            assert item["score"] is None
        finally:
            engine.dispose()

    def test_readings_after_a_move_do_not_score_the_earlier_event(self) -> None:
        """Readings taken in the new pot are not the old pot's response to the event."""
        client, engine = _make_client()
        try:
            _seed_cluster_with_devices(client)
            now = int(time.time())
            event_ts = now - 3 * 3600
            _seed_ten_point_rise(engine, event_ts, 3)  # 30 % before, 40 % at +1200, 41 % at +2400
            with Session(engine) as session:
                session.add(SensorReading(sensor_id=1, timestamp=event_ts + 3600, soil_moisture=90.0))
                session.add(SensorReading(sensor_id=1, timestamp=event_ts + 4200, soil_moisture=90.0))
                session.commit()
            _seed_moved_sensor(engine, plant_cluster_before=1, move_ts=event_ts + 3000)

            item = client.get("/api/v1/clusters/1/efficacy?days=1").json()["items"][0]
            assert item["before_pct"] == 30.0  # noqa: PLR2004 — the seeded baseline
            assert item["after_pct"] == 40.5  # noqa: PLR2004 — mean of the old pot's two readings
        finally:
            engine.dispose()


def test_days_is_bounded_like_the_web_page(seeded_client: TestClient) -> None:
    """D17: the API rejects ``days`` above 365 (422), as the web efficacy page always did."""
    assert seeded_client.get("/api/v1/clusters/1/efficacy?days=365").status_code == HTTPStatus.OK
    assert seeded_client.get("/api/v1/clusters/1/efficacy?days=366").status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert seeded_client.get("/clusters/1/efficacy?days=366").status_code == HTTPStatus.UNPROCESSABLE_ENTITY
