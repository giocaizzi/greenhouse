"""Tests for the multi-metric overlay chart API endpoint."""

import time


def test_overlay_returns_valid_shape(seeded_client):
    """Overlay endpoint returns 200 with the expected top-level keys."""
    resp = seeded_client.get("/api/v1/clusters/1/overlay?hours=72")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cluster_id"] == 1
    assert body["hours"] == 72
    assert body["normalised"] is True
    assert isinstance(body["datasets"], list)
    assert isinstance(body["events"], list)


def test_overlay_dataset_structure(seeded_client):
    """Each dataset must have metric + points fields with values in range."""
    # Seed readings first via the app fixture by seeding directly through the engine
    resp = seeded_client.get("/api/v1/clusters/1/overlay?hours=72")
    assert resp.status_code == 200
    for ds in resp.json()["datasets"]:
        assert "metric" in ds
        assert ds["metric"] in ("soil", "humidity", "light")
        assert isinstance(ds["points"], list)
        for pt in ds["points"]:
            assert len(pt) == 2
            ts, val = pt
            assert isinstance(ts, int)
            assert 0.0 <= val <= 100.0


def test_overlay_404_unknown_cluster(client):
    resp = client.get("/api/v1/clusters/9999/overlay")
    assert resp.status_code == 404


def test_overlay_with_readings(app):
    """Seed readings and assert all three metric datasets are returned."""
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

    cluster_id = repo.add_cluster("Overlay Test")
    sensor_id = repo.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="fake_overlay_s",
        name="Overlay Sensor",
        sensor_type="combined",
        config={},
    )
    now = int(time.time())
    for i in range(5):
        repo.add_sensor_reading(
            sensor_id=sensor_id,
            timestamp=now - i * 3600,
            soil_moisture=60.0 - i,
            env_humidity=55.0 + i,
            light=3000 + i * 100,
        )
    session.commit()

    app.dependency_overrides[get_repository] = lambda: repo
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(f"/api/v1/clusters/{cluster_id}/overlay?hours=72")
    assert resp.status_code == 200
    body = resp.json()
    metrics = {ds["metric"] for ds in body["datasets"]}
    assert "soil" in metrics
    assert "humidity" in metrics
    assert "light" in metrics

    # Each dataset must have at least one point
    for ds in body["datasets"]:
        assert len(ds["points"]) >= 1

    # All values must be in [0, 100]
    for ds in body["datasets"]:
        for _, val in ds["points"]:
            assert 0.0 <= val <= 100.0

    # Light dataset must carry original_max
    light_ds = next(ds for ds in body["datasets"] if ds["metric"] == "light")
    assert light_ds["original_max"] == 10_000.0

    app.dependency_overrides.pop(get_repository, None)
    session.close()
    engine.dispose()
