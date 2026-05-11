"""Tests for the plant health timeline API endpoint."""

import time


def test_health_timeline_empty_no_readings(seeded_client):
    """Plant with no moisture readings returns an empty points list."""
    resp = seeded_client.get("/api/v1/plants/1/health-timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["plant_id"] == 1
    assert isinstance(body["points"], list)
    assert "thresholds" in body
    assert body["thresholds"]["good"] == 80.0
    assert body["thresholds"]["ok"] == 50.0


def test_health_timeline_404_unknown_plant(client):
    resp = client.get("/api/v1/plants/9999/health-timeline")
    assert resp.status_code == 404


def test_health_timeline_point_structure(seeded_client):
    """Each point is a (unix_timestamp, score) tuple with score in [0, 100]."""
    resp = seeded_client.get("/api/v1/plants/1/health-timeline")
    assert resp.status_code == 200
    for ts, score in resp.json()["points"]:
        assert isinstance(ts, int)
        assert 0.0 <= score <= 100.0


def test_health_timeline_points_match_seeded_readings(app):
    """Seed N days of readings and assert points length matches."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from greenhouse_core.models import Base
    from greenhouse_core.repository import IrrigationRepository
    from greenhouse_server.deps import get_repository

    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    from sqlalchemy.orm import Session

    session = Session(engine)
    repo = IrrigationRepository(session)

    # Seed cluster, plant, sensor
    cluster_id = repo.add_cluster("Health Test")
    plant_id = repo.add_plant(cluster_id=cluster_id, species="Ficus lyrata")
    sensor_id = repo.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="fake_health_sensor",
        name="Health Sensor",
        sensor_type="soil",
        config={},
        plant_id=plant_id,
    )

    # Seed readings on 5 distinct days within the last 90 days
    now = int(time.time())
    distinct_days = 5
    for i in range(distinct_days):
        # Each reading is on a different calendar day (25h apart to ensure distinct UTC day buckets)
        ts = now - i * 25 * 3600
        repo.add_sensor_reading(sensor_id=sensor_id, timestamp=ts, soil_moisture=60.0 - i * 5)
    session.commit()

    app.dependency_overrides[get_repository] = lambda: repo
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(f"/api/v1/plants/{plant_id}/health-timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["points"]) == distinct_days

    app.dependency_overrides.pop(get_repository, None)
    session.close()
    engine.dispose()
