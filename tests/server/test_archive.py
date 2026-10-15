"""Soft delete (issue #149): archive, restore, and purge only after archive, for sensors and plants."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

from golden import FROZEN_TS
from greenhouse_core.logic.decision import TriggerCode
from greenhouse_core.logic.engine import IrrigationLogic
from greenhouse_core.models import ActivityEvent
from greenhouse_core.repository import IrrigationRepository

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

SENSOR_URL = "/api/v1/clusters/1/sensors/1"
PLANT_URL = "/api/v1/clusters/1/plants/1"
DEVICE_ID = "fake_tuya_device_aabbccdd"
FAKE_LOCAL_KEY = "fake_local_key_0000"  # a placeholder, never a real key
DRY_SOIL = 20.0


@pytest.fixture
def repo(app: FastAPI) -> Iterator[IrrigationRepository]:
    """A repository on the app's database, closed after the test."""
    session = app.state.session_factory()
    try:
        yield IrrigationRepository(session)
    finally:
        session.close()


@pytest.fixture
def seeded(frozen_clock: object, client: TestClient, repo: IrrigationRepository) -> TestClient:
    """Cluster 1, plant 1, sensor 1 on it (with a local key) and two dry readings, at the frozen instant."""
    del frozen_clock
    client.post("/api/v1/clusters", json={"name": "Archive Cluster"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Monstera deliciosa", "category": "tropical"})
    resp = client.post(
        "/api/v1/clusters/1/sensors",
        json={
            "tuya_device_id": DEVICE_ID,
            "name": "Archive Probe",
            "type": "tuya.tr301z",
            "plant_id": 1,
            "config": {"local_key": FAKE_LOCAL_KEY},
        },
    )
    assert resp.status_code == HTTPStatus.CREATED
    for age in (3600, 600):
        repo.add_sensor_reading(1, timestamp=FROZEN_TS - age, soil_moisture=DRY_SOIL)
    repo.commit()
    return client


def _events(repo: IrrigationRepository, code: str) -> list[ActivityEvent]:
    repo.session.expire_all()
    return [e for e in repo.list_activity_events(limit=50) if e.code == code]


def test_archive_then_restore_keeps_id_readings_and_assignments(
    seeded: TestClient, repo: IrrigationRepository
) -> None:
    """Archived: hidden from listings, still there with include_archived; restored: same id and history."""
    assert seeded.delete(SENSOR_URL).status_code == HTTPStatus.OK

    assert seeded.get("/api/v1/clusters/1/sensors").json() == []
    assert seeded.get("/api/v1/sensors").json()["sensors"] == []
    archived = seeded.get("/api/v1/clusters/1/sensors?include_archived=true").json()
    assert [(s["id"], s["archived_at"]) for s in archived] == [(1, FROZEN_TS)]

    restored = seeded.post(f"{SENSOR_URL}/restore")

    assert restored.status_code == HTTPStatus.OK
    assert (restored.json()["id"], restored.json()["archived_at"], restored.json()["plant_id"]) == (1, None, 1)
    rows = seeded.get("/api/v1/sensors/1/assignments").json()["assignments"]
    assert [(r["plant_id"], r["ended_at"]) for r in rows] == [(1, FROZEN_TS), (1, None)]
    assert len(repo.get_recent_readings(1, hours=24)) == 2  # noqa: PLR2004 — the two seeded readings


def test_archive_writes_an_audit_event_without_the_local_key(seeded: TestClient, repo: IrrigationRepository) -> None:
    """The trail names the device and counts its readings; the local key never appears."""
    seeded.delete(SENSOR_URL)

    (event,) = _events(repo, "sensor_archived")

    payload = json.loads(event.payload_json or "{}")
    assert payload == {
        "name": "Archive Probe",
        "tuya_device_id": DEVICE_ID,
        "cluster_id": 1,
        "plant_id": 1,
        "reading_count": 2,
    }
    assert FAKE_LOCAL_KEY not in (event.payload_json or "") + event.message


def test_purge_needs_a_prior_archive(seeded: TestClient, repo: IrrigationRepository) -> None:
    """Purge on an active sensor is a 409; after archiving it hard-deletes and frees the device id."""
    resp = seeded.delete(f"{SENSOR_URL}?purge=true")
    assert resp.status_code == HTTPStatus.CONFLICT
    assert "not archived" in resp.json()["detail"]

    seeded.delete(SENSOR_URL)
    assert seeded.delete(f"{SENSOR_URL}?purge=true").status_code == HTTPStatus.OK

    assert seeded.get("/api/v1/clusters/1/sensors?include_archived=true").json() == []
    assert repo.get_recent_readings(1, hours=24) == []
    assert len(_events(repo, "sensor_purged")) == 1
    again = seeded.post(
        "/api/v1/clusters/1/sensors", json={"tuya_device_id": DEVICE_ID, "name": "New", "type": "tuya.tr301z"}
    )
    assert again.status_code == HTTPStatus.CREATED


def test_re_registering_an_archived_device_id_points_at_the_archived_sensor(seeded: TestClient) -> None:
    """The archived row still holds the unique device id; the 409 says how to free it."""
    seeded.delete(SENSOR_URL)
    resp = seeded.post(
        "/api/v1/clusters/1/sensors", json={"tuya_device_id": DEVICE_ID, "name": "Dup", "type": "tuya.tr301z"}
    )
    assert resp.status_code == HTTPStatus.CONFLICT
    assert "archived sensor 1" in resp.json()["detail"]


def test_archived_sensor_no_longer_drives_decisions(
    seeded: TestClient, repo: IrrigationRepository, app: FastAPI
) -> None:
    """Its dry readings are out of the decision snapshot once archived."""
    seeded.delete(SENSOR_URL)
    repo.session.expire_all()

    decision = IrrigationLogic(repo, app.state.plant_db).decide_for_cluster(1)

    assert decision is not None
    assert decision.primary_code not in {TriggerCode.SENSOR_VERY_DRY, TriggerCode.SENSOR_DRY}
    assert decision.sensor_snapshot is None or decision.sensor_snapshot.per_sensor == []


def test_archived_sensor_is_out_of_the_system_health_pulse(seeded: TestClient) -> None:
    """The health pulse counts active sensors only."""
    assert seeded.get("/api/v1/health/system").json()["sensors_total"] == 1
    seeded.delete(SENSOR_URL)
    assert seeded.get("/api/v1/health/system").json()["sensors_total"] == 0


def test_plant_archive_restore_and_purge(seeded: TestClient, repo: IrrigationRepository) -> None:
    """Archive unlinks the sensors and skips snapshots; restore brings the plant back; purge needs the archive."""
    assert seeded.delete(f"{PLANT_URL}?purge=true").status_code == HTTPStatus.CONFLICT
    assert seeded.delete(PLANT_URL).status_code == HTTPStatus.OK

    assert seeded.get("/api/v1/clusters/1/plants").json() == []
    assert seeded.get("/api/v1/plants").json()["plants"] == []
    assert seeded.get(SENSOR_URL).json()["plant_id"] is None
    assert seeded.post("/api/v1/plants/health/snapshot").json()["rows_written"] == 0
    assert json.loads(_events(repo, "plant_archived")[0].payload_json or "{}")["unlinked_sensor_ids"] == [1]

    restored = seeded.post(f"{PLANT_URL}/restore")
    assert (restored.status_code, restored.json()["archived_at"]) == (HTTPStatus.OK, None)

    seeded.delete(PLANT_URL)
    assert seeded.delete(f"{PLANT_URL}?purge=true").status_code == HTTPStatus.OK
    assert seeded.get("/api/v1/clusters/1/plants?include_archived=true").json() == []


def test_a_sensor_cannot_be_linked_to_an_archived_plant(seeded: TestClient) -> None:
    """An archived plant is not one of the cluster's plants for a new link."""
    seeded.delete(PLANT_URL)
    resp = seeded.post(f"{SENSOR_URL}/move", json={"plant_id": 1})
    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_web_delete_archives(seeded: TestClient) -> None:
    """The web UI's delete button archives too (same rule as the API default)."""
    assert seeded.delete("/clusters/1/sensors/1").status_code == HTTPStatus.OK
    archived = seeded.get("/api/v1/clusters/1/sensors?include_archived=true").json()
    assert archived[0]["archived_at"] == FROZEN_TS
