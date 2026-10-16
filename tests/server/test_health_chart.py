"""Plant and cluster health charts: daily snapshots per component, the look-back floor, the live point."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from golden import FROZEN_TS
from greenhouse_core.constants import HEALTH_CHART_MIN_DAYS, SECONDS_PER_DAY
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_server.services.health import PlantHealthService

if TYPE_CHECKING:
    from time_machine import Traveller

    from greenhouse_core.models import Plant
    from greenhouse_core.repository import IrrigationRepository

TODAY = datetime.fromtimestamp(FROZEN_TS, tz=UTC).strftime("%Y-%m-%d")


@pytest.fixture
def repo(tmp_db: IrrigationRepository, frozen_clock: Traveller) -> IrrigationRepository:
    """The in-memory repository under a frozen clock."""
    del frozen_clock  # requested only for its side effect
    return tmp_db


def _plant(repo: IrrigationRepository) -> Plant:
    cid = repo.add_cluster("Chart Cluster")
    pid = repo.add_plant(cluster_id=cid, species="Dracaena", category="tropical")
    plant = repo.get_plant(pid)
    assert plant is not None
    return plant


def _snapshot(repo: IrrigationRepository, plant_id: int, days_ago: int, score: float) -> None:
    ts = FROZEN_TS - days_ago * SECONDS_PER_DAY
    repo.upsert_plant_health(
        plant_id=plant_id,
        date_key=datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d"),
        score=score,
        soil_in_band_pct=score - 10,
        temp_in_band_pct=None,
        humidity_in_band_pct=90.0,
        efficiency=0.5,
        sample_count=10,
        timestamp=ts,
    )


def test_short_range_shows_at_least_the_floor_of_daily_points(repo: IrrigationRepository) -> None:
    """A 3-day range still reads ``HEALTH_CHART_MIN_DAYS`` of snapshots; older ones stay out."""
    plant = _plant(repo)
    for days_ago, score in ((10, 40.0), (6, 60.0), (2, 70.0), (0, 75.0)):
        _snapshot(repo, plant.id, days_ago, score)

    chart = PlantHealthService(repo, PlantDatabase()).health_chart(plant, 3)

    assert chart["days"] == HEALTH_CHART_MIN_DAYS
    assert chart["species"] == "Dracaena"
    by_key = {s["key"]: s["points"] for s in chart["series"]}
    # No readings: no live score, so today's snapshot is the last point.
    assert by_key["score"] == [
        (FROZEN_TS - 6 * SECONDS_PER_DAY, 60.0),
        (FROZEN_TS - 2 * SECONDS_PER_DAY, 70.0),
        (FROZEN_TS, 75.0),
    ]
    assert by_key["soil_in_band_pct"] == [(ts, score - 10) for ts, score in by_key["score"]]
    assert by_key["temp_in_band_pct"] == []  # a component without data is an empty series
    assert {v for _, v in by_key["efficiency"]} == {50.0}  # efficiency on the score's 0-100 scale
    assert [s["key"] for s in chart["series"]] == [
        "score",
        "soil_in_band_pct",
        "temp_in_band_pct",
        "humidity_in_band_pct",
        "efficiency",
    ]


def test_long_range_keeps_the_requested_days(repo: IrrigationRepository) -> None:
    """A range above the floor is used as is."""
    plant = _plant(repo)
    _snapshot(repo, plant.id, 10, 40.0)

    chart = PlantHealthService(repo, PlantDatabase()).health_chart(plant, 14)

    assert chart["days"] == 14  # noqa: PLR2004 — the requested range
    assert chart["series"][0]["points"] == [(FROZEN_TS - 10 * SECONDS_PER_DAY, 40.0)]


def test_live_score_replaces_todays_snapshot(repo: IrrigationRepository) -> None:
    """With readings, the last point is the live score at now, not today's stored row."""
    plant = _plant(repo)
    sid = repo.add_sensor(
        cluster_id=plant.cluster_id,
        tuya_device_id="fake_tuya_device_aabbccdd",
        name="Chart Sensor",
        sensor_type="tuya.tr301z",
        config={},
        plant_id=plant.id,
        assignment_started_at=FROZEN_TS - 30 * SECONDS_PER_DAY,
    )
    repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 3600, soil_moisture=50.0)
    _snapshot(repo, plant.id, 1, 30.0)
    _snapshot(repo, plant.id, 0, 10.0)
    repo.session.commit()
    svc = PlantHealthService(repo, PlantDatabase())
    live = svc.compute_score(plant.id)["score"]
    assert live is not None

    chart = svc.health_chart(plant, 3)

    assert chart["series"][0]["points"] == [(FROZEN_TS - SECONDS_PER_DAY, 30.0), (FROZEN_TS, live)]


def test_cluster_chart_has_one_score_line_per_plant(repo: IrrigationRepository) -> None:
    """Each plant's line carries the score points of its own chart; a shared species gets the plant id."""
    plant = _plant(repo)
    twin_id = repo.add_plant(cluster_id=plant.cluster_id, species="Dracaena", category="tropical")
    fern_id = repo.add_plant(cluster_id=plant.cluster_id, species="Nephrolepis", category="tropical")
    _snapshot(repo, plant.id, 2, 70.0)
    _snapshot(repo, fern_id, 1, 55.0)
    plants = repo.get_plants_in_cluster(plant.cluster_id)

    chart = PlantHealthService(repo, PlantDatabase()).cluster_health_chart(plants, 3)

    assert chart["days"] == HEALTH_CHART_MIN_DAYS
    assert chart["plants"] == [
        {"plant_id": plant.id, "label": f"Dracaena #{plant.id}", "points": [(FROZEN_TS - 2 * SECONDS_PER_DAY, 70.0)]},
        {"plant_id": twin_id, "label": f"Dracaena #{twin_id}", "points": []},
        {"plant_id": fern_id, "label": "Nephrolepis", "points": [(FROZEN_TS - SECONDS_PER_DAY, 55.0)]},
    ]
