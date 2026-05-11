"""Plant dashboard — hero card + health ring."""

import time

import sqlalchemy
from sqlalchemy.orm import Session

from greenhouse_core.repository import IrrigationRepository


def test_plant_hero_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/plants/1")
    assert resp.status_code == 200
    assert 'class="plant-hero"' in resp.text


def test_plant_hero_ring_hidden_without_readings(seeded_client):
    """With no sensor readings the health ring shows the '—' placeholder."""
    resp = seeded_client.get("/clusters/1/plants/1")
    assert resp.status_code == 200
    # Score is None → health-ring--empty variant is shown
    assert "health-ring" in resp.text
    assert "health-ring--empty" in resp.text


def test_plant_hero_ring_shows_score_with_readings(seeded_client, app):
    """With readings present, the health ring markup is rendered."""
    ts = int(time.time())
    with Session(app.state.session_factory.kw["bind"]) as session:
        repo = IrrigationRepository(session)
        for i in range(5):
            repo.session.execute(
                sqlalchemy.text(
                    "INSERT INTO sensor_readings (sensor_id, timestamp, soil_moisture) VALUES (:sid, :ts, :sm)"
                ),
                {"sid": 1, "ts": ts - i * 3600, "sm": 55.0},
            )
        session.commit()

    resp = seeded_client.get("/clusters/1/plants/1")
    assert resp.status_code == 200
    assert 'class="health-ring' in resp.text


def test_plant_hero_404_for_wrong_cluster(seeded_client):
    resp = seeded_client.get("/clusters/999/plants/1")
    assert resp.status_code == 404
