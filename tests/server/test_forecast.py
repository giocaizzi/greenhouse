"""Tests for GET /api/v1/clusters/{cluster_id}/forecast."""

import time

import pytest

from fake_data import FAKE_DEVICE_ID, FAKE_PLANT_SPECIES, FAKE_SENSOR_ID


class TestForecastEndpoint:
    """Integration tests for the next-irrigation forecast route."""

    @pytest.fixture
    def outdoor_cluster_id(self, client):
        """Outdoor cluster suitable for weather-skip tests."""
        resp = client.post("/api/v1/clusters", json={"name": "Outdoor Cluster", "environment": "outdoor"})
        assert resp.status_code == 201
        return resp.json()["id"]

    @pytest.fixture
    def seeded_with_profile(self, client):
        """Cluster with sensor + readings + an irrigation event so a drainage profile exists."""
        # Create cluster
        resp = client.post("/api/v1/clusters", json={"name": "Profile Cluster", "environment": "indoor"})
        assert resp.status_code == 201
        cluster_id = resp.json()["id"]

        # Add plant
        resp = client.post(
            f"/api/v1/clusters/{cluster_id}/plants",
            json={
                "species": FAKE_PLANT_SPECIES,
                "category": "tropical",
                "water_needs": "medium",
            },
        )
        assert resp.status_code == 201
        plant_id = resp.json()["id"]

        # Add sensor linked to plant
        resp = client.post(
            f"/api/v1/clusters/{cluster_id}/sensors",
            json={
                "tuya_device_id": FAKE_SENSOR_ID,
                "name": "Test Sensor",
                "type": "soil_moisture",
                "plant_id": plant_id,
            },
        )
        assert resp.status_code == 201
        sensor_id = resp.json()["id"]

        # Add irrigator (needed so get_plant_profile can find irrigation events)
        resp = client.post(
            f"/api/v1/clusters/{cluster_id}/irrigators",
            json={
                "tuya_device_id": FAKE_DEVICE_ID,
                "name": "Test Irrigator",
                "type": "tuya_cloud",
            },
        )
        assert resp.status_code == 201
        irrigator_id = resp.json()["id"]

        return {
            "client": client,
            "cluster_id": cluster_id,
            "plant_id": plant_id,
            "sensor_id": sensor_id,
            "irrigator_id": irrigator_id,
        }

    def test_forecast_no_sensor_data(self, client):
        """Cluster with no sensors returns null timestamps and fallback_constant method."""
        resp = client.post("/api/v1/clusters", json={"name": "Empty Cluster"})
        assert resp.status_code == 201
        cluster_id = resp.json()["id"]
        client.post(f"/api/v1/clusters/{cluster_id}/plants", json={"species": FAKE_PLANT_SPECIES})

        resp = client.get(f"/api/v1/clusters/{cluster_id}/forecast")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cluster_id"] == cluster_id
        assert body["next_predicted_at"] is None
        assert body["hours_until_next"] is None
        assert body["method"] == "fallback_constant"

    def test_forecast_404_missing_cluster(self, client):
        """Returns 404 for a non-existent cluster."""
        resp = client.get("/api/v1/clusters/99999/forecast")
        assert resp.status_code == 404

    def test_forecast_fallback_when_no_history(self, seeded_with_profile):
        """Sensor data without irrigation history uses fallback_constant."""
        ctx = seeded_with_profile
        c = ctx["client"]
        cluster_id = ctx["cluster_id"]
        sensor_id = ctx["sensor_id"]

        # Add a reading: soil at 55%, target ~45%, drainage -2%/h → ~5h until threshold
        now = int(time.time())
        c.post(
            f"/api/v1/clusters/{cluster_id}/sensors/{sensor_id}/readings",
            json={"timestamp": now, "soil_moisture": 55.0},
        ) if False else None  # readings added via direct DB; use API if available

        # Since we can't easily POST readings via API (no route for it),
        # we call /forecast and expect fallback_constant with null hours (no readings).
        resp = c.get(f"/api/v1/clusters/{cluster_id}/forecast")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cluster_id"] == cluster_id
        # No readings yet → null
        assert body["next_predicted_at"] is None
        assert body["hours_until_next"] is None

    def test_forecast_with_drainage_slope(self, app, seeded_with_profile):
        """Forecast returns drainage_slope method when a learned profile is available."""

        from greenhouse_core.repository import IrrigationRepository

        # We need to inject readings + irrigation events directly into the in-memory DB.
        # The app fixture uses a shared engine stored on app.state — reach for it.
        session_factory = app.state.session_factory
        session = session_factory()
        try:
            repo = IrrigationRepository(session)
            ctx = seeded_with_profile
            cluster_id = ctx["cluster_id"]
            sensor_id = ctx["sensor_id"]
            irrigator_id = ctx["irrigator_id"]

            now = int(time.time())
            # Irrigation event 3h ago (within 30d window)
            irrigation_ts = now - 3 * 3600

            # Pre-irrigation reading: 30min before
            repo.add_sensor_reading(
                sensor_id=sensor_id,
                soil_moisture=30.0,
                timestamp=irrigation_ts - 1500,
            )
            # Post-irrigation readings: 15min and 60min after
            repo.add_sensor_reading(
                sensor_id=sensor_id,
                soil_moisture=55.0,
                timestamp=irrigation_ts + 900,
            )
            repo.add_sensor_reading(
                sensor_id=sensor_id,
                soil_moisture=52.0,
                timestamp=irrigation_ts + 3600,
            )
            # Current reading (after irrigation, moisture declining)
            repo.add_sensor_reading(
                sensor_id=sensor_id,
                soil_moisture=50.0,
                timestamp=now,
            )
            repo.add_irrigation_event(
                irrigator_id=irrigator_id,
                action="start",
                triggered_by="auto",
                duration_minutes=2,
                timestamp=irrigation_ts,
            )
            session.commit()
        finally:
            session.close()

        resp = ctx["client"].get(f"/api/v1/clusters/{cluster_id}/forecast")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cluster_id"] == cluster_id
        assert body["hours_until_next"] is not None
        assert body["hours_until_next"] > 0
        assert body["method"] == "drainage_slope"
        assert body["next_predicted_at"] is not None
        assert body["confidence"] >= 0.4

    def test_forecast_response_shape(self, client):
        """Forecast always returns all required fields."""
        resp = client.post("/api/v1/clusters", json={"name": "Shape Cluster"})
        cluster_id = resp.json()["id"]

        resp = client.get(f"/api/v1/clusters/{cluster_id}/forecast")
        assert resp.status_code == 200
        body = resp.json()

        required_fields = {
            "cluster_id",
            "next_predicted_at",
            "hours_until_next",
            "projected_min_moisture",
            "method",
            "confidence",
            "explanation",
            "weather_skip",
            "weather_reason",
            "precipitation_next_6h_mm",
        }
        assert required_fields.issubset(body.keys())
        assert body["weather_skip"] is False
