"""Tests for per-plant health score endpoints."""

import time

import pytest

from greenhouse_core.repository import IrrigationRepository


def _seed_plant_with_sensor(client) -> tuple[int, int]:
    """Create cluster→plant→sensor; return (plant_id, sensor_id)."""
    client.post("/api/v1/clusters", json={"name": "HC"})
    client.post(
        "/api/v1/clusters/1/plants",
        json={
            "species": "Monstera deliciosa",
            "category": "tropical",
            "ideal_temp_min": 18.0,
            "ideal_temp_max": 27.0,
            "ideal_humidity_min": 60.0,
            "ideal_humidity_max": 80.0,
        },
    )
    client.post(
        "/api/v1/clusters/1/sensors",
        json={"tuya_device_id": "fake_health_sensor", "name": "HS", "type": "soil_moisture", "plant_id": 1},
    )
    return 1, 1


def _repo(app) -> IrrigationRepository:
    session = app.state.session_factory()
    return IrrigationRepository(session)


class TestPlantHealthEndpoints:
    """GET /plants/{id}/health and POST /plants/health/snapshot."""

    def test_spike_readings_do_not_move_the_in_band_ratio(self, client, app):
        """Health is a judgement, so it reads the cleaned view like the engine.

        Eight in-band samples plus one 2% glitch: counted raw the plant loses
        ~11 points of in-band time for a reading the anomaly scan already flags
        as drift.
        """
        plant_id, sensor_id = _seed_plant_with_sensor(client)
        now = int(time.time())

        repo = _repo(app)
        for i, moisture in enumerate([55.0, 54.0, 56.0, 55.0, 2.0, 54.0, 55.0, 56.0, 55.0]):
            repo.add_sensor_reading(sensor_id=sensor_id, timestamp=now - 3600 * i, soil_moisture=moisture)
        repo.session.commit()

        client.post("/api/v1/plants/health/snapshot")
        history = client.get(f"/api/v1/plants/{plant_id}/health").json()["history"]

        assert history[0]["soil_in_band_pct"] == pytest.approx(100.0)

    def test_get_health_score_in_band(self, client, app):
        """Score is between 0 and 100; snapshot soil_in_band_pct reflects in-band ratio."""
        plant_id, sensor_id = _seed_plant_with_sensor(client)
        now = int(time.time())

        # care data for Monstera: soil target 45-65 (from plant_database.json)
        # 3 in-band, 1 out-of-band → soil_in_band_pct = 75.0
        repo = _repo(app)
        for i, moisture in enumerate([55.0, 50.0, 60.0, 30.0]):
            repo.add_sensor_reading(sensor_id=sensor_id, timestamp=now - 3600 * i, soil_moisture=moisture)
        repo.session.commit()

        resp = client.get(f"/api/v1/plants/{plant_id}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["plant_id"] == plant_id
        assert data["species"] == "Monstera deliciosa"
        assert data["current_score"] is not None
        assert 0 <= data["current_score"] <= 100

        # Persist snapshot and verify soil_in_band_pct stored correctly
        client.post("/api/v1/plants/health/snapshot")
        hist_resp = client.get(f"/api/v1/plants/{plant_id}/health")
        history = hist_resp.json()["history"]
        assert len(history) == 1
        assert history[0]["soil_in_band_pct"] == pytest.approx(75.0)

    def test_get_health_zero_readings_returns_none_score(self, client, app):
        """Plant with no sensor readings yields current_score=None and empty history."""
        plant_id, _ = _seed_plant_with_sensor(client)
        resp = client.get(f"/api/v1/plants/{plant_id}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_score"] is None
        assert data["history"] == []

    def test_get_health_404_unknown_plant(self, client, app):
        resp = client.get("/api/v1/plants/9999/health")
        assert resp.status_code == 404

    def test_snapshot_creates_daily_row(self, client, app):
        """POST /plants/health/snapshot writes a row and GET history returns it."""
        plant_id, sensor_id = _seed_plant_with_sensor(client)
        now = int(time.time())
        repo = _repo(app)
        for i in range(3):
            repo.add_sensor_reading(sensor_id=sensor_id, timestamp=now - 3600 * i, soil_moisture=55.0)
        repo.session.commit()

        snap_resp = client.post("/api/v1/plants/health/snapshot")
        assert snap_resp.status_code == 200
        assert snap_resp.json()["rows_written"] == 1

        hist_resp = client.get(f"/api/v1/plants/{plant_id}/health")
        assert hist_resp.status_code == 200
        assert len(hist_resp.json()["history"]) == 1

    def test_snapshot_no_data_skips_plant(self, client, app):
        """Snapshot skips plants with no readings; rows_written = 0."""
        _seed_plant_with_sensor(client)
        resp = client.post("/api/v1/plants/health/snapshot")
        assert resp.status_code == 200
        assert resp.json()["rows_written"] == 0

    def test_snapshot_idempotent(self, client, app):
        """Calling snapshot twice on the same day keeps history at 1 entry."""
        plant_id, sensor_id = _seed_plant_with_sensor(client)
        now = int(time.time())
        repo = _repo(app)
        repo.add_sensor_reading(sensor_id=sensor_id, timestamp=now, soil_moisture=55.0)
        repo.session.commit()

        client.post("/api/v1/plants/health/snapshot")
        client.post("/api/v1/plants/health/snapshot")

        hist_resp = client.get(f"/api/v1/plants/{plant_id}/health")
        assert len(hist_resp.json()["history"]) == 1
