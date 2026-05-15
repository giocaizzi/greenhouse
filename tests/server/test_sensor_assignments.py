"""Tests for the SensorAssignment history table and assignment-aware queries."""

from __future__ import annotations

import time

import pytest


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
            "type": "soil_moisture",
            "plant_id": 1,
        },
    )
    assert resp.status_code == 201
    return {"client": client, "cluster_id": 1, "plant_a": 1, "plant_b": 2, "sensor_id": 1}


def _set_plant(client, sensor_id, plant_id):
    return client.put(f"/api/v1/clusters/1/sensors/{sensor_id}", json={"plant_id": plant_id})


def test_creating_sensor_with_plant_opens_assignment(seeded):
    client = seeded["client"]
    resp = client.get(f"/api/v1/sensors/{seeded['sensor_id']}/assignments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sensor_id"] == seeded["sensor_id"]
    assert len(body["assignments"]) == 1
    row = body["assignments"][0]
    assert row["plant_id"] == seeded["plant_a"]
    assert row["ended_at"] is None


def test_reassign_closes_old_and_opens_new(seeded):
    client = seeded["client"]
    resp = _set_plant(client, seeded["sensor_id"], seeded["plant_b"])
    assert resp.status_code == 200
    rows = client.get(f"/api/v1/sensors/{seeded['sensor_id']}/assignments").json()["assignments"]
    assert len(rows) == 2
    rows.sort(key=lambda r: r["started_at"])
    assert rows[0]["plant_id"] == seeded["plant_a"]
    assert rows[0]["ended_at"] is not None  # closed
    assert rows[1]["plant_id"] == seeded["plant_b"]
    assert rows[1]["ended_at"] is None  # current


def test_reassign_to_same_plant_is_noop(seeded):
    client = seeded["client"]
    _set_plant(client, seeded["sensor_id"], seeded["plant_a"])
    rows = client.get(f"/api/v1/sensors/{seeded['sensor_id']}/assignments").json()["assignments"]
    assert len(rows) == 1  # nothing new opened


def test_assignment_endpoint_404_for_missing_sensor(client):
    assert client.get("/api/v1/sensors/9999/assignments").status_code == 404


def test_readings_for_plant_respects_assignment_window(client, app):
    # Timeline: sensor created on plant A at T0, reading_A at T0+1s, reassign to
    # plant B at "now", reading_B at "now" — readings_for_plant must split by
    # which plant owned the sensor at reading time.
    client.post("/api/v1/clusters", json={"name": "Window Cluster"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Monstera deliciosa"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Dypsis lutescens"})
    resp = client.post(
        "/api/v1/clusters/1/sensors",
        json={
            "tuya_device_id": "fake_window_sensor",
            "name": "Window Probe",
            "type": "soil_moisture",
            "plant_id": 1,
        },
    )
    assert resp.status_code == 201

    # First reading lands while sensor is still on plant A. Wait a moment so
    # the reading timestamp is strictly inside the open assignment window.
    time.sleep(1.1)
    session = app.state.session_factory()
    try:
        from greenhouse_core.repository import IrrigationRepository

        repo = IrrigationRepository(session)
        repo.add_sensor_reading(sensor_id=1, timestamp=int(time.time()), soil_moisture=42.0)
        session.commit()
    finally:
        session.close()

    # Reassign sensor to plant B — closes plant A's assignment, opens plant B's.
    time.sleep(1.1)
    resp = client.put("/api/v1/clusters/1/sensors/1", json={"plant_id": 2})
    assert resp.status_code == 200

    # Reading after reassignment.
    time.sleep(1.1)
    session = app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        repo.add_sensor_reading(sensor_id=1, timestamp=int(time.time()), soil_moisture=80.0)
        session.commit()

        plant_a_soil = [r.soil_moisture for r in repo.readings_for_plant(plant_id=1, since_ts=0)]
        plant_b_soil = [r.soil_moisture for r in repo.readings_for_plant(plant_id=2, since_ts=0)]
        assert 42.0 in plant_a_soil
        assert 80.0 not in plant_a_soil
        assert 80.0 in plant_b_soil
        assert 42.0 not in plant_b_soil
    finally:
        session.close()


def test_plant_chart_uses_assignment_window(client, app):
    """The plant chart must not retroactively re-attribute readings on sensor move."""
    client.post("/api/v1/clusters", json={"name": "Chart Cluster"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Monstera deliciosa"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Dypsis lutescens"})
    client.post(
        "/api/v1/clusters/1/sensors",
        json={
            "tuya_device_id": "fake_chart_sensor",
            "name": "Chart Probe",
            "type": "soil_moisture",
            "plant_id": 1,
        },
    )

    time.sleep(1.1)
    session = app.state.session_factory()
    try:
        from greenhouse_core.repository import IrrigationRepository

        repo = IrrigationRepository(session)
        repo.add_sensor_reading(sensor_id=1, timestamp=int(time.time()), soil_moisture=33.0)
        session.commit()
    finally:
        session.close()

    # Move sensor to plant B.
    time.sleep(1.1)
    client.put("/api/v1/clusters/1/sensors/1", json={"plant_id": 2})

    # New reading after move belongs to plant B.
    time.sleep(1.1)
    session = app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        repo.add_sensor_reading(sensor_id=1, timestamp=int(time.time()), soil_moisture=66.0)
        session.commit()
    finally:
        session.close()

    # Plant A's chart shows 33.0 only.
    a = client.get("/api/v1/plants/1/chart-data?hours=24&metric=soil_moisture").json()
    a_points = [p for d in a["datasets"] for p in d["points"]]
    a_vals = [p[1] for p in a_points]
    assert 33.0 in a_vals
    assert 66.0 not in a_vals

    # Plant B's chart shows 66.0 only.
    b = client.get("/api/v1/plants/2/chart-data?hours=24&metric=soil_moisture").json()
    b_points = [p for d in b["datasets"] for p in d["points"]]
    b_vals = [p[1] for p in b_points]
    assert 66.0 in b_vals
    assert 33.0 not in b_vals


def test_delete_plant_closes_open_assignment(client, app):
    client.post("/api/v1/clusters", json={"name": "Delete Cluster"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Monstera deliciosa"})
    client.post(
        "/api/v1/clusters/1/sensors",
        json={"tuya_device_id": "fake_delete_sensor", "name": "Probe", "type": "soil_moisture", "plant_id": 1},
    )
    # Sensor has one open assignment now.
    rows = client.get("/api/v1/sensors/1/assignments").json()["assignments"]
    assert rows[0]["ended_at"] is None

    # Delete plant — should close the open row and orphan the sensor.
    resp = client.delete("/api/v1/clusters/1/plants/1")
    assert resp.status_code == 200

    rows_after = client.get("/api/v1/sensors/1/assignments").json()["assignments"]
    assert len(rows_after) == 1
    assert rows_after[0]["ended_at"] is not None
