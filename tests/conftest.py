"""Shared pytest fixtures for greenhouse test suite."""

import os
import time

import pytest
import time_machine
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
from golden import ENV_PREFIXES, FROZEN_INSTANT
from greenhouse_core.models import Base
from greenhouse_core.repository import IrrigationRepository


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
        irrigator_type="rainpoint.ik10pw",
        config={},
    )
    sensor_id = tmp_db.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id=FAKE_SENSOR_ID,
        name=FAKE_SENSOR_NAME,
        sensor_type="tuya.tr301z",
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


# ── Determinism kit for the characterization / golden suite (see tests/golden.py) ──


@pytest.fixture
def frozen_clock():
    """Freeze wall-clock time (``time.time``, ``datetime.now``) at ``FROZEN_INSTANT``.

    Monotonic clocks keep running, so asyncio / Textual timers still work.
    """
    with time_machine.travel(FROZEN_INSTANT, tick=False) as traveller:
        yield traveller


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Hermetic environment: no app env vars, no ``.env`` file in cwd, UTC local time, plain terminal."""
    for name in list(os.environ):
        if name.startswith(ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("NO_COLOR", "1")
    time.tzset()
    yield tmp_path
    monkeypatch.undo()
    time.tzset()
