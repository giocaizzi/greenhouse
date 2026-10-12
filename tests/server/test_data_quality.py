"""Tests for GET /api/v1/quality/report."""

from http import HTTPStatus

from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from greenhouse_server.app import create_app
from greenhouse_server.config import Settings
from greenhouse_server.deps import get_device_gateway, get_device_registry


def _make_client() -> tuple[TestClient, Engine]:
    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    settings = Settings(db_url="sqlite://", enable_scheduler=False, auth_enabled=False)
    app = create_app(settings, engine=engine)
    app.dependency_overrides[get_device_registry] = lambda: None
    app.dependency_overrides[get_device_gateway] = lambda: None
    return TestClient(app, raise_server_exceptions=False), engine


class TestDataQualityReport:
    """Tests for data quality report."""

    def test_empty_db_no_issues(self, client: TestClient) -> None:
        """Empty DB no issues."""
        resp = client.get("/api/v1/quality/report")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["issues"] == []
        assert data["counts"] == {}

    def test_sensor_without_plant_detected(self) -> None:
        """Sensor without plant detected."""
        client, engine = _make_client()
        try:
            client.post("/api/v1/clusters", json={"name": "Quality Cluster"})
            client.post(
                "/api/v1/clusters/1/sensors",
                json={"tuya_device_id": "fake_unassigned_sensor", "name": "Orphan Sensor", "type": "tuya.tr301z"},
            )
            resp = client.get("/api/v1/quality/report")
            data = resp.json()
            codes = [i["code"] for i in data["issues"]]
            assert "sensor_without_plant" in codes
            assert data["counts"].get("sensor_without_plant", 0) >= 1
        finally:
            engine.dispose()

    def test_plant_without_sensor_detected(self) -> None:
        """Plant without sensor detected."""
        client, engine = _make_client()
        try:
            client.post("/api/v1/clusters", json={"name": "Quality Cluster"})
            client.post("/api/v1/clusters/1/plants", json={"species": "Fern", "water_needs": "high"})
            resp = client.get("/api/v1/quality/report")
            data = resp.json()
            codes = [i["code"] for i in data["issues"]]
            assert "plant_without_sensor" in codes
        finally:
            engine.dispose()

    def test_duplicate_tuya_id_critical(self) -> None:
        """Two devices sharing the same tuya_device_id must be flagged as critical."""
        client, engine = _make_client()
        try:
            client.post("/api/v1/clusters", json={"name": "Quality Cluster"})
            client.post(
                "/api/v1/clusters/1/sensors",
                json={"tuya_device_id": "shared_id_aabbcc", "name": "Sensor A", "type": "tuya.tr301z"},
            )
            client.post(
                "/api/v1/clusters/1/irrigator",
                json={"tuya_device_id": "shared_id_aabbcc", "name": "Irrigator A", "type": "rainpoint.ik10pw"},
            )
            resp = client.get("/api/v1/quality/report")
            data = resp.json()
            dupes = [i for i in data["issues"] if i["code"] == "duplicate_tuya_id"]
            assert len(dupes) >= 1
            assert all(d["severity"] == "critical" for d in dupes)
        finally:
            engine.dispose()

    def test_counts_aggregation(self) -> None:
        """Counts aggregation."""
        client, engine = _make_client()
        try:
            client.post("/api/v1/clusters", json={"name": "Count Cluster"})
            species = ["Rose A", "Rose B"]
            for name in species:
                client.post("/api/v1/clusters/1/plants", json={"species": name})
            resp = client.get("/api/v1/quality/report")
            data = resp.json()
            assert data["counts"].get("plant_without_sensor", 0) == len(species)
        finally:
            engine.dispose()
