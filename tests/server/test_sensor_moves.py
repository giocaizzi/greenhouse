"""Sensor moves (issue #145): readings are attributed by the assignment window everywhere.

Plant A and plant B share cluster 1; sensor 1 starts on A. Readings are seeded
relative to the frozen clock, so every boundary below is exact.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

from golden import FROZEN_TS
from greenhouse_core.constants import SENSOR_MOVE_SETTLE_SECONDS
from greenhouse_core.logic.decision import TriggerCode
from greenhouse_core.logic.engine import IrrigationLogic
from greenhouse_core.models import SensorAssignment
from greenhouse_core.repository import IrrigationRepository

if TYPE_CHECKING:
    from collections.abc import Iterator

    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

HOUR = 3600
PLANT_A, PLANT_B, SENSOR = 1, 2, 1
MOVE_URL = f"/api/v1/clusters/1/sensors/{SENSOR}/move"
OLD_POT_SOIL = 20.0  # dry: measured in plant A's pot
SETTLING_SOIL = 99.0  # handling noise right after the move
NEW_POT_SOIL = 55.0  # plant B's real state


@pytest.fixture
def repo(app: FastAPI) -> Iterator[IrrigationRepository]:
    """A repository on the app's database, closed after the test."""
    session = app.state.session_factory()
    try:
        yield IrrigationRepository(session)
    finally:
        session.close()


@pytest.fixture
def two_plants(frozen_clock: object, client: TestClient) -> TestClient:
    """Cluster 1, plants A (Monstera) and B (Dypsis), sensor 1 on plant A, at the frozen instant."""
    del frozen_clock
    client.post("/api/v1/clusters", json={"name": "Move Cluster"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Monstera deliciosa", "category": "tropical"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Dypsis lutescens", "category": "tropical"})
    resp = client.post(
        "/api/v1/clusters/1/sensors",
        json={
            "tuya_device_id": "fake_tuya_device_aabbccdd",
            "name": "Move Probe",
            "type": "tuya.tr301z",
            "plant_id": 1,
        },
    )
    assert resp.status_code == HTTPStatus.CREATED
    return client


def _seed(repo: IrrigationRepository, *readings: tuple[int, float]) -> None:
    """Soil readings on sensor 1, each ``(age_seconds, soil)`` before the frozen instant."""
    for age, soil in readings:
        repo.add_sensor_reading(SENSOR, timestamp=FROZEN_TS - age, soil_moisture=soil)
    repo.commit()


def _move(client: TestClient, plant_id: int | None, effective_at: int | None = None) -> httpx.Response:
    body: dict[str, int | None] = {"plant_id": plant_id}
    if effective_at is not None:
        body["effective_at"] = effective_at
    resp: httpx.Response = client.post(MOVE_URL, json=body)
    return resp


def _soil(repo: IrrigationRepository, plant_id: int) -> list[float | None]:
    repo.session.expire_all()
    return [r.soil_moisture for r in repo.readings_for_plant(plant_id, since_ts=0)]


def _move_two_hours_ago(client: TestClient, repo: IrrigationRepository) -> None:
    """Old-pot readings before the move, a settling reading and a settled one after it."""
    _seed(
        repo,
        (3 * HOUR, OLD_POT_SOIL),
        (2 * HOUR + 600, OLD_POT_SOIL),
        (2 * HOUR - 600, SETTLING_SOIL),
        (HOUR, NEW_POT_SOIL),
    )
    assert _move(client, PLANT_B, FROZEN_TS - 2 * HOUR).status_code == HTTPStatus.OK


def test_move_splits_attribution_at_effective_at(two_plants: TestClient, repo: IrrigationRepository) -> None:
    """Readings before ``effective_at`` stay with A (pre-registration backfill included); later ones go to B."""
    _move_two_hours_ago(two_plants, repo)

    assert _soil(repo, PLANT_A) == [OLD_POT_SOIL, OLD_POT_SOIL]
    assert _soil(repo, PLANT_B) == [SETTLING_SOIL, NEW_POT_SOIL]  # the chart shows the settle window raw
    judged = repo.judged_readings_for_plant(PLANT_B, since_ts=0)
    assert [r.soil_moisture for r in judged[SENSOR]] == [NEW_POT_SOIL]


def test_health_score_and_snapshot_follow_the_assignment(two_plants: TestClient, repo: IrrigationRepository) -> None:
    """A's score uses only the pre-move readings, B's only the settled post-move one; snapshots name the sensor."""
    _move_two_hours_ago(two_plants, repo)

    assert two_plants.post("/api/v1/plants/health/snapshot").status_code == HTTPStatus.OK
    a_rows = two_plants.get(f"/api/v1/plants/{PLANT_A}/health").json()["history"]
    b_rows = two_plants.get(f"/api/v1/plants/{PLANT_B}/health").json()["history"]
    assert [(r["sample_count"], r["sensor_ids"]) for r in a_rows] == [(2, [SENSOR])]
    assert [(r["sample_count"], r["sensor_ids"]) for r in b_rows] == [(1, [SENSOR])]


def test_back_dating_a_move_re_attributes_the_readings_in_between(
    two_plants: TestClient, repo: IrrigationRepository
) -> None:
    """A move entered 5 h late, then re-dated to when it happened, moves those 5 h of readings to B."""
    _seed(repo, (6 * HOUR, OLD_POT_SOIL), (4 * HOUR, NEW_POT_SOIL), (2 * HOUR, NEW_POT_SOIL))
    assert _move(two_plants, PLANT_B).status_code == HTTPStatus.OK
    assert _soil(repo, PLANT_B) == []

    resp = _move(two_plants, PLANT_B, FROZEN_TS - 5 * HOUR)

    assert resp.status_code == HTTPStatus.OK
    windows = [(a["plant_id"], a["started_at"], a["ended_at"]) for a in resp.json()["assignments"]]
    redated = FROZEN_TS - 5 * HOUR
    assert windows == [(PLANT_A, redated, redated), (PLANT_B, redated, None)]  # A's first link still reaches back
    assert _soil(repo, PLANT_A) == [OLD_POT_SOIL]
    assert _soil(repo, PLANT_B) == [NEW_POT_SOIL, NEW_POT_SOIL]


@pytest.mark.parametrize(
    ("plant_id", "offset", "fragment"),
    [
        (PLANT_B, HOUR, "must not be in the future"),
        (PLANT_B, -HOUR, "must be at or after the current assignment's start"),
    ],
)
def test_invalid_effective_at_is_a_400_naming_the_field(
    two_plants: TestClient, plant_id: int, offset: int, fragment: str
) -> None:
    """A future time, or one before the current link began, is refused and nothing changes."""
    assert _move(two_plants, PLANT_B).status_code == HTTPStatus.OK  # current link starts now

    resp = _move(two_plants, PLANT_A if offset < 0 else plant_id, FROZEN_TS + offset)

    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert resp.json()["detail"].startswith("effective_at: ")
    assert fragment in resp.json()["detail"]


def test_re_dating_before_the_previous_assignment_is_refused(two_plants: TestClient) -> None:
    """Re-dating may not empty the previous (non-first) assignment: its start is the floor."""
    assert _move(two_plants, PLANT_B, FROZEN_TS - 3 * HOUR).status_code == HTTPStatus.OK
    assert _move(two_plants, PLANT_A, FROZEN_TS - HOUR).status_code == HTTPStatus.OK

    resp = _move(two_plants, PLANT_A, FROZEN_TS - 4 * HOUR)

    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert resp.json()["detail"].startswith("effective_at: must be after the previous assignment's start")


def test_move_to_a_plant_of_another_cluster_is_a_404(two_plants: TestClient) -> None:
    """The target plant must belong to the sensor's cluster."""
    assert _move(two_plants, 999).status_code == HTTPStatus.NOT_FOUND


def test_detach_then_readings_belong_to_no_plant(two_plants: TestClient, repo: IrrigationRepository) -> None:
    """``plant_id: null`` closes the link: later readings are nobody's."""
    assert _move(two_plants, None, FROZEN_TS - HOUR).status_code == HTTPStatus.OK
    _seed(repo, (2 * HOUR, OLD_POT_SOIL), (HOUR // 2, NEW_POT_SOIL))
    assert _soil(repo, PLANT_A) == [OLD_POT_SOIL]
    assert repo.judged_since(SENSOR) == FROZEN_TS - HOUR + SENSOR_MOVE_SETTLE_SECONDS


def test_first_decision_after_a_move_ignores_the_old_pot(
    two_plants: TestClient, repo: IrrigationRepository, app: FastAPI
) -> None:
    """Moved 10 min ago: the dry old-pot reading neither drives the call nor counts; the trail says why."""
    del two_plants
    _seed(repo, (HOUR, OLD_POT_SOIL), (300, NEW_POT_SOIL))
    repo.move_sensor(SENSOR, PLANT_B, when=FROZEN_TS - 600)
    repo.commit()

    decision = IrrigationLogic(repo, app.state.plant_db).decide_for_cluster(1)

    assert decision is not None
    codes = [r.code for r in decision.reasons]
    # Without the move rule the 20 % old-pot reading would drive a very-dry irrigation.
    assert codes == [TriggerCode.NO_DATA, TriggerCode.SENSOR_SETTLING]


def test_learning_response_is_attributed_to_the_plant_measured_at_the_event(
    two_plants: TestClient, repo: IrrigationRepository
) -> None:
    """``plant_of_sensor_at`` answers from the window that holds the timestamp."""
    assert _move(two_plants, PLANT_B, FROZEN_TS - HOUR).status_code == HTTPStatus.OK
    assert repo.plant_of_sensor_at(SENSOR, FROZEN_TS - 2 * HOUR) == PLANT_A
    assert repo.plant_of_sensor_at(SENSOR, FROZEN_TS - 10) == PLANT_B


def test_two_open_assignments_for_one_sensor_cannot_be_inserted(
    two_plants: TestClient, repo: IrrigationRepository
) -> None:
    """The partial unique index refuses a second open row."""
    del two_plants
    repo.session.add(SensorAssignment(sensor_id=SENSOR, plant_id=PLANT_B, started_at=FROZEN_TS, ended_at=None))
    with pytest.raises(IntegrityError):
        repo.session.flush()


def test_a_recycled_plant_id_does_not_inherit_old_assignments(
    two_plants: TestClient, repo: IrrigationRepository
) -> None:
    """Foreign keys are not enforced, so deleted plant B's closed rows stay: a new plant must get a fresh id."""
    assert _move(two_plants, PLANT_B, FROZEN_TS - HOUR).status_code == HTTPStatus.OK
    _seed(repo, (HOUR // 2, NEW_POT_SOIL))
    assert two_plants.delete(f"/api/v1/clusters/1/plants/{PLANT_B}").status_code == HTTPStatus.OK

    resp = two_plants.post("/api/v1/clusters/1/plants", json={"species": "Ficus elastica"})

    assert resp.json()["id"] not in {PLANT_A, PLANT_B}
    assert _soil(repo, resp.json()["id"]) == []
