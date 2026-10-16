"""Web UI for issue #179: dated sensor moves, and restore / purge of archived plants and sensors."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

from golden import FROZEN_TS
from greenhouse_core import utils

if TYPE_CHECKING:
    import time_machine
    from fastapi.testclient import TestClient

HOUR = 3600
DEVICE_ID = "fake_tuya_device_aabbccdd"


@pytest.fixture
def seeded(frozen_clock: time_machine.Traveller, client: TestClient) -> TestClient:
    """Cluster 1 with plants 1 and 2 and sensor 1 on plant 1, created at the frozen instant."""
    del frozen_clock
    client.post("/api/v1/clusters", json={"name": "Archive Cluster"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Monstera deliciosa"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Ficus lyrata"})
    resp = client.post(
        "/api/v1/clusters/1/sensors",
        json={"tuya_device_id": DEVICE_ID, "name": "Probe A", "type": "tuya.tr301z", "plant_id": 1},
    )
    assert resp.status_code == HTTPStatus.CREATED
    return client


def _assignments(client: TestClient) -> list[tuple[int | None, int, int | None]]:
    rows = client.get("/api/v1/sensors/1/assignments").json()["assignments"]
    return [(r["plant_id"], r["started_at"], r["ended_at"]) for r in rows]


def test_move_dates_the_move_in_the_timezone_preference(
    seeded: TestClient, frozen_clock: time_machine.Traveller, monkeypatch: pytest.MonkeyPatch
) -> None:
    """13:00 in Rome (UTC+2 in April) is 11:00 UTC: the boundary lands there, not at "now"."""
    monkeypatch.setattr(utils, "_display_timezone", "Europe/Rome")
    frozen_clock.shift(2 * HOUR)

    resp = seeded.post(
        "/clusters/1/sensors/1/move",
        data={"plant_id": "2", "effective_at": "2026-04-15T13:00"},
        follow_redirects=False,
    )

    assert resp.status_code == HTTPStatus.SEE_OTHER
    assert resp.headers["location"] == "/clusters/1#sensors"
    assert _assignments(seeded) == [(1, FROZEN_TS, FROZEN_TS + HOUR), (2, FROZEN_TS + HOUR, None)]


def test_move_blank_plant_detaches_now(seeded: TestClient, frozen_clock: time_machine.Traveller) -> None:
    """Blank plant = detach; blank time = now."""
    frozen_clock.shift(HOUR)

    seeded.post("/clusters/1/sensors/1/move", data={"plant_id": "", "effective_at": ""})

    assert _assignments(seeded) == [(1, FROZEN_TS, FROZEN_TS + HOUR)]
    assert seeded.get("/api/v1/clusters/1/sensors").json()[0]["plant_id"] is None


@pytest.mark.parametrize(
    ("data", "status", "detail"),
    [
        ({"plant_id": "2", "effective_at": "2026-04-15T10:01"}, 400, "effective_at: must not be in the future"),
        ({"plant_id": "abc"}, 400, "Invalid plant_id"),
        ({"plant_id": "2", "effective_at": "yesterday"}, 400, "Invalid effective_at"),
        ({"plant_id": "99"}, 404, "Plant 99 not found in cluster"),
    ],
)
def test_move_refusals_use_the_api_rules(
    seeded: TestClient, monkeypatch: pytest.MonkeyPatch, data: dict[str, str], status: int, detail: str
) -> None:
    """A future or malformed time, a malformed plant id and a foreign plant are refused."""
    monkeypatch.setattr(utils, "_display_timezone", "UTC")

    resp = seeded.post("/clusters/1/sensors/1/move", data=data, headers={"Accept": "application/json"})

    assert resp.status_code == status
    assert detail in resp.text
    assert _assignments(seeded) == [(1, FROZEN_TS, None)]


def test_archived_card_lists_archived_rows_only(seeded: TestClient) -> None:
    """No card while nothing is archived; archived rows show with restore and purge actions."""
    assert 'id="archived"' not in seeded.get("/clusters/1").text

    seeded.delete("/clusters/1/sensors/1")
    seeded.delete("/clusters/1/plants/2")
    page = seeded.get("/clusters/1").text

    assert 'id="archived"' in page
    assert 'action="/clusters/1/sensors/1/restore"' in page
    assert 'hx-delete="/clusters/1/sensors/1?purge=true"' in page
    assert 'action="/clusters/1/plants/2/restore"' in page
    assert 'hx-delete="/clusters/1/plants/2?purge=true"' in page
    assert "/clusters/1/plants/1/restore" not in page


def test_restore_brings_sensor_and_plant_back(seeded: TestClient) -> None:
    """Restore redirects to the section and the row is active again (sensor re-linked to its plant)."""
    seeded.delete("/clusters/1/sensors/1")
    seeded.delete("/clusters/1/plants/2")

    sensor = seeded.post("/clusters/1/sensors/1/restore", follow_redirects=False)
    plant = seeded.post("/clusters/1/plants/2/restore", follow_redirects=False)

    assert (sensor.status_code, sensor.headers["location"]) == (HTTPStatus.SEE_OTHER, "/clusters/1#sensors")
    assert (plant.status_code, plant.headers["location"]) == (HTTPStatus.SEE_OTHER, "/clusters/1#plants")
    assert [s["plant_id"] for s in seeded.get("/api/v1/clusters/1/sensors").json()] == [1]
    assert sorted(p["id"] for p in seeded.get("/api/v1/clusters/1/plants").json()) == [1, 2]


@pytest.mark.parametrize(("path", "listing"), [("sensors/1", "sensors"), ("plants/2", "plants")])
def test_purge_needs_archive_first(seeded: TestClient, path: str, listing: str) -> None:
    """Purging an active row is a 409 and changes nothing; once archived it is gone for good."""
    refused = seeded.delete(f"/clusters/1/{path}?purge=true", headers={"Accept": "application/json"})
    assert refused.status_code == HTTPStatus.CONFLICT
    assert "archive it first" in refused.text

    seeded.delete(f"/clusters/1/{path}")
    purged = seeded.delete(f"/clusters/1/{path}?purge=true")

    assert (purged.status_code, purged.text) == (HTTPStatus.OK, "")
    remaining = seeded.get(f"/api/v1/clusters/1/{listing}?include_archived=true").json()
    assert int(path.split("/")[1]) not in [r["id"] for r in remaining]


def test_sensor_edit_page_has_the_move_form(seeded: TestClient) -> None:
    """The edit page offers the dated move with a detach option and a datetime-local input."""
    page = seeded.get("/clusters/1/sensors/1/edit").text

    assert 'action="/clusters/1/sensors/1/move"' in page
    assert "— detach —" in page
    assert 'type="datetime-local"' in page
