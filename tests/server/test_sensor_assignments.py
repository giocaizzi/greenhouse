"""Tests for the SensorAssignment history table and assignment-aware queries."""

from __future__ import annotations

import time
from http import HTTPStatus

import pytest

from greenhouse_core.repository import IrrigationRepository


@pytest.fixture
def seeded(client):
    """Cluster + plant A + plant B + sensor (no plant yet) + readings spanning both windows."""
    client.post("/api/v1/clusters", json={"name": "Assignment Cluster"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Monstera deliciosa"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Dypsis lutescens"})
    resp = client.post(
        "/api/v1/clusters/1/sensors",
        json={
            "tuya_device_id": "fake_assignment_sensor",
            "name": "Assignment Probe",
            "type": "tuya.tr301z",
            "plant_id": 1,
        },
    )
    assert resp.status_code == HTTPStatus.CREATED
    return {"client": client, "cluster_id": 1, "plant_a": 1, "plant_b": 2, "sensor_id": 1}


def _set_plant(client, sensor_id, plant_id):
    return client.put(f"/api/v1/clusters/1/sensors/{sensor_id}", json={"plant_id": plant_id})


def test_creating_sensor_with_plant_opens_assignment(seeded):
    """Creating sensor with plant opens assignment."""
    client = seeded["client"]
    resp = client.get(f"/api/v1/sensors/{seeded['sensor_id']}/assignments")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["sensor_id"] == seeded["sensor_id"]
    assert len(body["assignments"]) == 1
    row = body["assignments"][0]
    assert row["plant_id"] == seeded["plant_a"]
    assert row["ended_at"] is None


def test_reassign_closes_old_and_opens_new(seeded):
    """Reassign closes old and opens new."""
    client = seeded["client"]
    resp = _set_plant(client, seeded["sensor_id"], seeded["plant_b"])
    assert resp.status_code == HTTPStatus.OK
    rows = client.get(f"/api/v1/sensors/{seeded['sensor_id']}/assignments").json()["assignments"]
    rows.sort(key=lambda r: r["started_at"])
    assert [r["plant_id"] for r in rows] == [seeded["plant_a"], seeded["plant_b"]]
    assert rows[0]["plant_id"] == seeded["plant_a"]
    assert rows[0]["ended_at"] is not None  # closed
    assert rows[1]["plant_id"] == seeded["plant_b"]
    assert rows[1]["ended_at"] is None  # current


def test_reassign_to_same_plant_is_noop(seeded):
    """Reassign to same plant is noop."""
    client = seeded["client"]
    _set_plant(client, seeded["sensor_id"], seeded["plant_a"])
    rows = client.get(f"/api/v1/sensors/{seeded['sensor_id']}/assignments").json()["assignments"]
    assert len(rows) == 1  # nothing new opened


def test_assignment_endpoint_404_for_missing_sensor(client):
    """Assignment endpoint 404 for missing sensor."""
    assert client.get("/api/v1/sensors/9999/assignments").status_code == HTTPStatus.NOT_FOUND


def _two_plants_and_a_probe(client, cluster: str, device_id: str, probe: str):
    """Cluster 1 with plants 1 (Monstera) and 2 (Dypsis) and sensor 1 on plant 1; return the sensor response."""
    client.post("/api/v1/clusters", json={"name": cluster})
    client.post("/api/v1/clusters/1/plants", json={"species": "Monstera deliciosa"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Dypsis lutescens"})
    return client.post(
        "/api/v1/clusters/1/sensors",
        json={"tuya_device_id": device_id, "name": probe, "type": "tuya.tr301z", "plant_id": 1},
    )


def _add_reading_now(app, soil: float) -> None:
    """Persist one soil reading on sensor 1, stamped with the current second."""
    session = app.state.session_factory()
    try:
        IrrigationRepository(session).add_sensor_reading(sensor_id=1, timestamp=int(time.time()), soil_moisture=soil)
        session.commit()
    finally:
        session.close()


def _soil_by_plant(app, plant_id: int) -> list[float]:
    """Soil values ``readings_for_plant`` attributes to ``plant_id``."""
    session = app.state.session_factory()
    try:
        return [
            r.soil_moisture for r in IrrigationRepository(session).readings_for_plant(plant_id=plant_id, since_ts=0)
        ]
    finally:
        session.close()


def _chart_soil(client, plant_id: int) -> list[float]:
    """Soil values on ``plant_id``'s 24 h chart."""
    chart = client.get(f"/api/v1/plants/{plant_id}/chart-data?hours=24&metric=soil_moisture").json()
    return [p[1] for d in chart["datasets"] for p in d["points"]]


def test_readings_for_plant_respects_assignment_window(client, app):
    # Timeline: sensor created on plant A at T0, reading_A at T0+1s, reassign to
    # plant B at "now", reading_B at "now" — readings_for_plant must split by
    # which plant owned the sensor at reading time.
    """Readings for plant respects assignment window."""
    resp = _two_plants_and_a_probe(client, "Window Cluster", "fake_window_sensor", "Window Probe")
    assert resp.status_code == HTTPStatus.CREATED

    before_move, after_move = 42.0, 80.0
    # First reading lands while sensor is still on plant A. Wait a moment so
    # the reading timestamp is strictly inside the open assignment window.
    time.sleep(1.1)
    _add_reading_now(app, before_move)

    # Reassign sensor to plant B — closes plant A's assignment, opens plant B's.
    time.sleep(1.1)
    resp = client.put("/api/v1/clusters/1/sensors/1", json={"plant_id": 2})
    assert resp.status_code == HTTPStatus.OK

    # Reading after reassignment.
    time.sleep(1.1)
    _add_reading_now(app, after_move)

    plant_a_soil = _soil_by_plant(app, 1)
    plant_b_soil = _soil_by_plant(app, 2)
    assert before_move in plant_a_soil
    assert after_move not in plant_a_soil
    assert after_move in plant_b_soil
    assert before_move not in plant_b_soil


def test_plant_chart_uses_assignment_window(client, app):
    """The plant chart must not retroactively re-attribute readings on sensor move."""
    _two_plants_and_a_probe(client, "Chart Cluster", "fake_chart_sensor", "Chart Probe")

    before_move, after_move = 33.0, 66.0
    time.sleep(1.1)
    _add_reading_now(app, before_move)

    # Move sensor to plant B.
    time.sleep(1.1)
    client.put("/api/v1/clusters/1/sensors/1", json={"plant_id": 2})

    # New reading after move belongs to plant B.
    time.sleep(1.1)
    _add_reading_now(app, after_move)

    # Plant A's chart shows 33.0 only.
    a_vals = _chart_soil(client, 1)
    assert before_move in a_vals
    assert after_move not in a_vals

    # Plant B's chart shows 66.0 only.
    b_vals = _chart_soil(client, 2)
    assert after_move in b_vals
    assert before_move not in b_vals


@pytest.mark.usefixtures("app")
def test_delete_plant_closes_open_assignment(client):
    """Delete plant closes open assignment."""
    client.post("/api/v1/clusters", json={"name": "Delete Cluster"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Monstera deliciosa"})
    client.post(
        "/api/v1/clusters/1/sensors",
        json={"tuya_device_id": "fake_delete_sensor", "name": "Probe", "type": "tuya.tr301z", "plant_id": 1},
    )
    # Sensor has one open assignment now.
    rows = client.get("/api/v1/sensors/1/assignments").json()["assignments"]
    assert rows[0]["ended_at"] is None

    # Delete plant — should close the open row and orphan the sensor.
    resp = client.delete("/api/v1/clusters/1/plants/1")
    assert resp.status_code == HTTPStatus.OK

    rows_after = client.get("/api/v1/sensors/1/assignments").json()["assignments"]
    assert len(rows_after) == 1
    assert rows_after[0]["ended_at"] is not None
