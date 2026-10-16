"""Soil range strip on the home cluster card (issue #201)."""

import time
from collections.abc import Iterator
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from greenhouse_core.constants import SENSOR_READING_STALE_SECONDS
from greenhouse_core.models import Base
from greenhouse_core.plant_db import get_plant_database
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.soil_strip import soil_strip
from server.conftest import app_of

MIDPOINT = 40.0


@pytest.fixture
def repo() -> Iterator[IrrigationRepository]:
    """A repository on a fresh in-memory database."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield IrrigationRepository(session)
    engine.dispose()


def _plant(repo: IrrigationRepository, cluster_id: int, species: str, target: str) -> int:
    plant_id = repo.add_plant(cluster_id, species, category="tropical")
    repo.update_plant(plant_id, soil_moisture_target=target)
    return plant_id


def _sensor(repo: IrrigationRepository, cluster_id: int, plant_id: int | None, *readings: tuple[int, float]) -> None:
    n = len(repo.get_sensors_in_cluster(cluster_id))
    sid = repo.add_sensor(
        cluster_id, f"fake_tuya_device_{n:08x}", f"S{n}", "tuya.tr301z", {}, plant_id=plant_id, assignment_started_at=0
    )
    for age, soil in readings:
        repo.add_sensor_reading(sid, timestamp=int(time.time()) - age, soil_moisture=soil)


def test_strip_judges_each_plant_against_its_own_band(repo: IrrigationRepository) -> None:
    """A plant's mark is dry/wet against its own target; the shaded band is the cluster's widest."""
    cid = repo.add_cluster("C")
    cactus, fern = _plant(repo, cid, "Cactus", "10-30"), _plant(repo, cid, "Fern", "50-70")
    _sensor(repo, cid, cactus, (60, 40.0))
    _sensor(repo, cid, fern, (60, 40.0))
    strip = soil_strip(repo, get_plant_database(), cid, repo.get_plants_in_cluster(cid))
    assert strip is not None
    assert strip["band"] == (10.0, 70.0)
    assert [(m["plant"], m["position"]) for m in strip["marks"]] == [("Cactus", "wet"), ("Fern", "dry")]
    assert strip["average"] == MIDPOINT


def test_strip_takes_the_driest_current_sensor_per_plant(repo: IrrigationRepository) -> None:
    """Like the engine: a plant with two sensors shows the drier; stale and unlinked readings are left out."""
    cid = repo.add_cluster("C")
    a, b = _plant(repo, cid, "A", "40-60"), _plant(repo, cid, "B", "40-60")
    _sensor(repo, cid, a, (60, 30.0))
    _sensor(repo, cid, a, (60, 55.0))
    _sensor(repo, cid, b, (60, 50.0))
    _sensor(repo, cid, b, (SENSOR_READING_STALE_SECONDS + 60, 5.0))
    _sensor(repo, cid, None, (60, 10.0))
    strip = soil_strip(repo, get_plant_database(), cid, repo.get_plants_in_cluster(cid))
    assert strip is not None
    assert [(m["plant"], m["value"]) for m in strip["marks"]] == [("A", 30.0), ("B", 50.0)]
    assert strip["average"] == MIDPOINT


def test_strip_without_plants_is_none_and_without_readings_has_no_average(repo: IrrigationRepository) -> None:
    """No plants → no strip; plants without readings → band only."""
    cid = repo.add_cluster("C")
    assert soil_strip(repo, get_plant_database(), cid, []) is None
    _plant(repo, cid, "A", "40-60")
    strip = soil_strip(repo, get_plant_database(), cid, repo.get_plants_in_cluster(cid))
    assert strip == {"band": (40.0, 60.0), "marks": [], "average": None}


def test_card_fragment_renders_the_strip(seeded_client: TestClient) -> None:
    """The card shows the band, the avg mark and the plant mark, with the numbers as a text alternative."""
    with app_of(seeded_client).state.session_factory() as session:
        repo = IrrigationRepository(session)
        repo.update_plant(1, soil_moisture_target="40-60")
        repo.add_sensor_reading(1, soil_moisture=30.0)
        repo.commit()
    resp = seeded_client.get("/clusters/1/card-fragment")
    assert resp.status_code == HTTPStatus.OK
    html = resp.text
    assert 'class="soil-strip"' in html
    assert "soil-strip__plant--off" in html
    assert ">avg</span>" in html
    assert "avg 30%, target 40–60%. Monstera deliciosa 30% (dry, band 40–60%). Values are cleaned" in html
    assert "1 of 1 out of band" in html


def test_card_fragment_strip_without_readings(seeded_client: TestClient) -> None:
    """A plant with no reading yet shows the band and says so instead of an avg mark."""
    html = seeded_client.get("/clusters/1/card-fragment").text
    assert "no current readings" in html
    assert "soil-strip__avg" not in html
