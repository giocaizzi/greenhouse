"""Soil range strip on the home cluster card (issue #201)."""

from http import HTTPStatus

from fastapi.testclient import TestClient

from greenhouse_core.constants import SENSOR_READING_STALE_SECONDS
from greenhouse_core.models import Plant, SensorReading
from greenhouse_core.plant_db import get_plant_database
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.cluster import SensorStatusRow
from greenhouse_server.services.soil_strip import soil_strip
from server.conftest import app_of

MIDPOINT = 40.0


def _plant(plant_id: int, species: str, target: str | None) -> Plant:
    return Plant(id=plant_id, cluster_id=1, species=species, category="tropical", soil_moisture_target=target)


def _row(sensor_id: int, plant_id: int | None, soil: float | None, age: int = 60) -> SensorStatusRow:
    reading = SensorReading(sensor_id=sensor_id, timestamp=0, soil_moisture=soil)
    return {
        "id": sensor_id,
        "name": f"S{sensor_id}",
        "type": "tuya.tr301z",
        "plant_id": plant_id,
        "last_reading": reading,
        "reading_age_seconds": age,
    }


def test_strip_judges_each_plant_against_its_own_band() -> None:
    """A plant's mark is dry/wet against its own target; the shaded band is the cluster's widest."""
    plants = [_plant(1, "Cactus", "10-30"), _plant(2, "Fern", "50-70")]
    strip = soil_strip(get_plant_database(), plants, [_row(1, 1, 40.0), _row(2, 2, 40.0)])
    assert strip is not None
    assert strip["band"] == (10.0, 70.0)
    assert [(m["plant"], m["position"]) for m in strip["marks"]] == [("Cactus", "wet"), ("Fern", "dry")]
    assert strip["average"] == MIDPOINT


def test_strip_average_is_the_mean_of_current_readings() -> None:
    """Average is the mean (display only); stale, missing and unlinked readings are left out."""
    plants = [_plant(1, "A", "40-60"), _plant(2, "B", "40-60"), _plant(3, "C", "40-60"), _plant(4, "D", "40-60")]
    rows = [
        _row(1, 1, 30.0),
        _row(2, 2, 50.0),
        _row(3, 3, 90.0, age=SENSOR_READING_STALE_SECONDS + 1),
        _row(4, 4, None),
        _row(5, None, 10.0),
    ]
    strip = soil_strip(get_plant_database(), plants, rows)
    assert strip is not None
    assert [m["plant"] for m in strip["marks"]] == ["A", "B"]
    assert strip["average"] == MIDPOINT


def test_strip_without_plants_is_none_and_without_readings_has_no_average() -> None:
    """No plants → no strip; plants without readings → band only."""
    assert soil_strip(get_plant_database(), [], []) is None
    strip = soil_strip(get_plant_database(), [_plant(1, "A", "40-60")], [])
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
    assert "avg 30%, target 40–60%. Monstera deliciosa 30% (dry, band 40–60%)." in html
    assert "1 of 1 out of band" in html


def test_card_fragment_strip_without_readings(seeded_client: TestClient) -> None:
    """A plant with no reading yet shows the band and says so instead of an avg mark."""
    html = seeded_client.get("/clusters/1/card-fragment").text
    assert "no current readings" in html
    assert "soil-strip__avg" not in html
