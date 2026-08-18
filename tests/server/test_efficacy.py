"""Tests for GET /api/v1/clusters/{cluster_id}/efficacy."""

import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from greenhouse_core.models import IrrigationEvent, Sensor, SensorReading
from greenhouse_server.app import create_app
from greenhouse_server.config import Settings
from greenhouse_server.deps import get_device_gateway, get_device_registry


def _make_client():
    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    settings = Settings(db_url="sqlite://", enable_scheduler=False, auth_enabled=False)
    app = create_app(settings, engine=engine)
    app.dependency_overrides[get_device_registry] = lambda: None
    app.dependency_overrides[get_device_gateway] = lambda: None
    return TestClient(app, raise_server_exceptions=False), engine


class TestClusterEfficacy:
    def test_not_found(self, client):
        resp = client.get("/api/v1/clusters/999/efficacy")
        assert resp.status_code == 404

    def test_empty_cluster(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/efficacy")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cluster_id"] == 1
        assert data["days"] == 14
        assert data["items"] == []

    def test_scored_event_with_moisture_rise(self):
        """10% moisture rise after an irrigation event → positive score."""
        client, engine = _make_client()
        try:
            client.post("/api/v1/clusters", json={"name": "Efficacy Cluster"})
            client.post(
                "/api/v1/clusters/1/sensors",
                json={"tuya_device_id": "fake_eff_sensor", "name": "Eff Sensor", "type": "soil_moisture"},
            )
            client.post(
                "/api/v1/clusters/1/irrigator",
                json={"tuya_device_id": "fake_eff_irrigator", "name": "Eff Pump", "type": "tuya_cloud"},
            )

            now = int(time.time())
            event_ts = now - 3600

            with Session(engine) as session:
                sensor = session.get(Sensor, 1)
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
                        duration_minutes=3,
                        triggered_by="auto",
                    )
                )
                session.commit()

            resp = client.get("/api/v1/clusters/1/efficacy?days=1")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert item["irrigator_name"] == "Eff Pump"
            assert item["duration_minutes"] == 3
            assert item["before_pct"] is not None
            assert item["after_pct"] is not None
            assert item["score"] is not None
            assert item["score"] > 0

        finally:
            engine.dispose()

    def test_spike_reading_does_not_inflate_the_score(self):
        """Efficacy is rise × 5, so one glitch sample is worth 100 phantom points.

        The event below is a dud — moisture sat at 30% before and after — with a
        single spurious 95% sample in the after-window. Read through the cleaned
        view that spike is dropped and the score stays at the floor.
        """
        client, engine = _make_client()
        try:
            client.post("/api/v1/clusters", json={"name": "Efficacy Cluster"})
            client.post(
                "/api/v1/clusters/1/sensors",
                json={"tuya_device_id": "fake_eff_sensor", "name": "Eff Sensor", "type": "soil_moisture"},
            )
            client.post(
                "/api/v1/clusters/1/irrigator",
                json={"tuya_device_id": "fake_eff_irrigator", "name": "Eff Pump", "type": "tuya_cloud"},
            )

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

            assert item["after_pct"] < 35.0
            assert item["score"] < 25.0
        finally:
            client.close()
            engine.dispose()

    def test_score_formula(self):
        """Exactly 20% rise should yield score 100."""
        client, engine = _make_client()
        try:
            client.post("/api/v1/clusters", json={"name": "Score Cluster"})
            client.post(
                "/api/v1/clusters/1/sensors",
                json={"tuya_device_id": "fake_score_sensor", "name": "S Sensor", "type": "soil_moisture"},
            )
            client.post(
                "/api/v1/clusters/1/irrigator",
                json={"tuya_device_id": "fake_score_pump", "name": "S Pump", "type": "tuya_cloud"},
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
            assert data["items"][0]["score"] == 100.0
        finally:
            engine.dispose()

    def test_days_query_param(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/efficacy?days=30")
        assert resp.status_code == 200
        assert resp.json()["days"] == 30
