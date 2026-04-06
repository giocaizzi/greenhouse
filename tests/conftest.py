"""Shared pytest fixtures for tuya_irrigation test suite."""

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fake_data import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FAKE_CLUSTER_NAME,
    FAKE_DEVICE_ID,
    FAKE_IRRIGATOR_NAME,
    FAKE_PLANT_SPECIES,
    FAKE_REGION,
    FAKE_SENSOR_ID,
    FAKE_SENSOR_NAME,
)
from tuya_irrigation_core.models import Base
from tuya_irrigation_core.repository import IrrigationRepository


@pytest.fixture
def tmp_db():
    """Create an in-memory IrrigationRepository for testing."""
    engine = create_engine("sqlite://", echo=False)
    Base.metadata.create_all(engine)
    session = Session(engine)
    repo = IrrigationRepository(session)
    yield repo
    session.close()
    engine.dispose()


@pytest.fixture
def fake_tuya_env(monkeypatch):
    """Patch environment with fake Tuya credentials."""
    monkeypatch.setenv("TUYA_CLIENT_ID", FAKE_CLIENT_ID)
    monkeypatch.setenv("TUYA_CLIENT_SECRET", FAKE_CLIENT_SECRET)
    monkeypatch.setenv("TUYA_REGION", FAKE_REGION)


@pytest.fixture
def sample_cluster(tmp_db):
    """Pre-populated cluster with a plant, irrigator, and sensor with readings."""
    cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
    plant_id = tmp_db.add_plant(
        cluster_id=cluster_id,
        species=FAKE_PLANT_SPECIES,
        category="tropical",
        water_needs="medium",
        ideal_temp_min=18.0,
        ideal_temp_max=27.0,
        ideal_humidity_min=60.0,
        ideal_humidity_max=80.0,
    )
    irrigator_id = tmp_db.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id=FAKE_DEVICE_ID,
        name=FAKE_IRRIGATOR_NAME,
        irrigator_type="tuya_cloud",
        config={},
    )
    sensor_id = tmp_db.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id=FAKE_SENSOR_ID,
        name=FAKE_SENSOR_NAME,
        sensor_type="soil_moisture",
        config={},
        plant_id=plant_id,
    )
    now = int(time.time())
    tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=now, soil_moisture=50.0, temperature=22.0)
    tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=now - 3600, soil_moisture=52.0, temperature=21.5)
    tmp_db.session.commit()

    return {
        "db": tmp_db,
        "cluster_id": cluster_id,
        "plant_id": plant_id,
        "irrigator_id": irrigator_id,
        "sensor_id": sensor_id,
    }
