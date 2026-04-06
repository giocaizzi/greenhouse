"""Server test fixtures — full-stack TestClient with in-memory SQLite."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from tuya_irrigation_server.app import create_app
from tuya_irrigation_server.config import Settings
from tuya_irrigation_server.deps import get_device_manager


@pytest.fixture
def app():
    """FastAPI app with in-memory SQLite and stubbed device manager."""
    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    settings = Settings(db_url="sqlite://")
    application = create_app(settings, engine=engine)

    # Stub device manager — returns success for all operations
    mock_dm = MagicMock()
    mock_dm.irrigator_start.return_value = (True, "Started OK")
    mock_dm.irrigator_off.return_value = (True, "Stopped OK")
    mock_dm.read_sensor.return_value = {"temperature": 22.0, "soil_moisture": 50.0}

    application.dependency_overrides[get_device_manager] = lambda: mock_dm

    return application


@pytest.fixture
def client(app):
    """TestClient backed by in-memory SQLite."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def seeded_client(client):
    """Client with a pre-populated cluster, plant, sensor, irrigator, and config."""
    # Create cluster
    resp = client.post("/api/v1/clusters", json={"name": "Test Cluster", "environment": "indoor"})
    assert resp.status_code == 201

    # Add plant
    resp = client.post(
        "/api/v1/clusters/1/plants",
        json={
            "species": "Monstera deliciosa",
            "category": "tropical",
            "water_needs": "medium",
            "ideal_temp_min": 18.0,
            "ideal_temp_max": 27.0,
            "ideal_humidity_min": 60.0,
            "ideal_humidity_max": 80.0,
        },
    )
    assert resp.status_code == 201

    # Add sensor
    resp = client.post(
        "/api/v1/clusters/1/sensors",
        json={
            "tuya_device_id": "fake_sensor_001",
            "name": "Test Sensor",
            "type": "soil_moisture",
            "plant_id": 1,
        },
    )
    assert resp.status_code == 201

    # Add irrigator
    resp = client.post(
        "/api/v1/clusters/1/irrigators",
        json={
            "tuya_device_id": "fake_irrigator_001",
            "name": "Test Irrigator",
            "type": "tuya_cloud",
        },
    )
    assert resp.status_code == 201

    # Set config
    resp = client.put(
        "/api/v1/clusters/1/config",
        json={"mode": "smart", "duration_minutes": 2, "interval_hours": 12, "auto_run": True},
    )
    assert resp.status_code == 200

    return client
