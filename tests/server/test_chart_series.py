"""Chart series are labelled by the plant the sensor was measuring at each moment."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from greenhouse_core.models import Base
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.charts import build_cluster_chart_payload

HOUR = 3600


@pytest.mark.usefixtures("frozen_clock")
def test_moved_sensor_splits_into_one_series_per_plant() -> None:
    """Plant A, then unassigned, then plant B: three series, each labelled by what it measured."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    repo = IrrigationRepository(Session(engine))
    now = int(time.time())
    cluster_id = repo.add_cluster("Living Room")
    plant_a = repo.add_plant(cluster_id=cluster_id, species="Ficus benjamina")
    plant_b = repo.add_plant(cluster_id=cluster_id, species="Dracaena marginata")
    sensor_id = repo.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="fake_tuya_device_aabbccdd",
        name="Sensor 1",
        sensor_type="soil",
        config={},
        plant_id=plant_a,
        assignment_started_at=now - 10 * HOUR,
    )
    repo.reassign_sensor_to_plant(sensor_id, None, when=now - 6 * HOUR)
    repo.reassign_sensor_to_plant(sensor_id, plant_b, when=now - 3 * HOUR)
    for hours_ago in (9, 8, 5, 2, 1):
        repo.add_sensor_reading(sensor_id=sensor_id, timestamp=now - hours_ago * HOUR, soil_moisture=40.0)

    datasets = build_cluster_chart_payload(repo, cluster_id, 24, "soil_moisture")["datasets"]

    assert [(d["label"], d["plant_id"], len(d["points"])) for d in datasets] == [
        ("Ficus benjamina · Sensor 1", plant_a, 2),
        ("Sensor 1 (unassigned)", None, 1),
        ("Dracaena marginata · Sensor 1", plant_b, 2),
    ]
    assert {d["sensor_name"] for d in datasets} == {"Sensor 1"}
