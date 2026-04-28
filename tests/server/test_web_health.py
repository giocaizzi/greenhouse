"""Web health page tests: full-page render, status badge, and stale sensor degraded state."""

import time


class TestHealthPage:
    def test_renders_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "System Health" in resp.text

    def test_shows_status_badge(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        # Empty system → status "down"
        assert "down" in resp.text

    def test_health_badge_fragment_still_works(self, client):
        """The _health_badge partial endpoint must still respond independently."""
        resp = client.get("/health/badge")
        assert resp.status_code == 200
        assert "scheduler" in resp.text.lower()

    def test_degraded_when_stale_sensor(self, app):
        """A sensor with no fresh reading + a fresh reading elsewhere → degraded."""
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            # Create cluster + sensor
            client.post("/api/v1/clusters", json={"name": "Health Test Cluster"})
            client.post(
                "/api/v1/clusters/1/sensors",
                json={"tuya_device_id": "fake_health_s1", "name": "Stale Sensor", "type": "soil_moisture"},
            )
            # Add another sensor to keep cloud reachable
            client.post(
                "/api/v1/clusters/1/sensors",
                json={"tuya_device_id": "fake_health_s2", "name": "Fresh Sensor", "type": "soil_moisture"},
            )

            from tuya_irrigation_core.models import Sensor, SensorReading

            now = int(time.time())
            session = app.state.session_factory()
            try:
                s1 = session.get(Sensor, 1)
                s2 = session.get(Sensor, 2)
                # Stale: older than 3 h
                session.add(SensorReading(sensor_id=s1.id, timestamp=now - 4 * 3600, soil_moisture=30.0))
                # Fresh: under 1 h → keeps cloud_reachable=True
                session.add(SensorReading(sensor_id=s2.id, timestamp=now - 600, soil_moisture=50.0))
                session.commit()
            finally:
                session.close()

            resp = client.get("/health")
            assert resp.status_code == 200
            assert "degraded" in resp.text
