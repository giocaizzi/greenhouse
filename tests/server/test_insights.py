"""Tests for GET /api/v1/clusters/{cluster_id}/insights."""


class TestClusterInsights:
    def test_insights_not_found(self, client):
        resp = client.get("/api/v1/clusters/999/insights")
        assert resp.status_code == 404

    def test_insights_fresh_cluster_no_alerts(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/insights")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cluster_id"] == 1
        assert data["cluster_name"] == "Test Cluster"
        assert isinstance(data["insights"], list)
        assert data["forecast"] is None

    def test_stale_sensor_surfaces_insight(self, app):
        """A sensor with no recent data must produce a stale_data insight."""
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        from greenhouse_server.app import create_app
        from greenhouse_server.config import Settings
        from greenhouse_server.deps import get_device_manager, get_tuya_cloud

        engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        settings = Settings(db_url="sqlite://", enable_scheduler=False)
        application = create_app(settings, engine=engine)
        application.dependency_overrides[get_device_manager] = lambda: None
        application.dependency_overrides[get_tuya_cloud] = lambda: None

        client = TestClient(application, raise_server_exceptions=False)

        client.post("/api/v1/clusters", json={"name": "Stale Cluster"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Monstera deliciosa", "water_needs": "medium"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={
                "tuya_device_id": "fake_stale_sensor",
                "name": "Stale Sensor",
                "type": "soil_moisture",
                "plant_id": 1,
            },
        )

        resp = client.get("/api/v1/clusters/1/insights")
        assert resp.status_code == 200
        data = resp.json()
        codes = [i["code"] for i in data["insights"]]
        assert "stale_data" in codes

        insight = next(i for i in data["insights"] if i["code"] == "stale_data")
        assert insight["severity"] == "warning"
        assert insight["title"] == "Stale sensor data"
        assert insight["suggestion"] is not None

        engine.dispose()
