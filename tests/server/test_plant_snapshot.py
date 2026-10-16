"""``plant_snapshots``: a tile per plant (soil judged on the cleaned view, climate shown raw) and current conditions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from golden import FROZEN_TS
from greenhouse_core.constants import SECONDS_PER_DAY, SECONDS_PER_HOUR, SENSOR_READING_STALE_SECONDS
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_server.services.cluster import ClusterService
from greenhouse_server.services.plant_snapshot import PlantSnapshots, plant_snapshots

if TYPE_CHECKING:
    from time_machine import Traveller

    from greenhouse_core.repository import IrrigationRepository

LINKED_SINCE = FROZEN_TS - 30 * SECONDS_PER_DAY


@pytest.fixture
def repo(tmp_db: IrrigationRepository, frozen_clock: Traveller) -> IrrigationRepository:
    """The in-memory repository under a frozen clock."""
    del frozen_clock  # requested only for its side effect
    return tmp_db


def _sensor(repo: IrrigationRepository, cluster_id: int, name: str, plant_id: int | None) -> int:
    return repo.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id=f"fake_tuya_device_{name.lower()}",
        name=name,
        sensor_type="tuya.tr301z",
        config={},
        plant_id=plant_id,
        assignment_started_at=LINKED_SINCE,
    )


def _snapshots(repo: IrrigationRepository, cluster_id: int) -> PlantSnapshots:
    plant_db = PlantDatabase()
    status = ClusterService(repo, plant_db).get_cluster_status(cluster_id)
    return plant_snapshots(repo, plant_db, cluster_id, status["plants"], status["sensors"])


def test_tile_judges_the_driest_sensor_against_the_plants_own_band(repo: IrrigationRepository) -> None:
    """Two probes in one pot: the drier one is the plant's soil; climate comes from the newest raw reading."""
    cid = repo.add_cluster("Snapshot Cluster")
    fern = repo.add_plant(cluster_id=cid, species="Nephrolepis", soil_moisture_target="40-60")
    cactus = repo.add_plant(cluster_id=cid, species="Echinocactus", soil_moisture_target="10-20")
    wet_probe = _sensor(repo, cid, "Fern A", fern)
    dry_probe = _sensor(repo, cid, "Fern B", fern)
    cactus_probe = _sensor(repo, cid, "Cactus", cactus)
    repo.add_sensor_reading(sensor_id=wet_probe, timestamp=FROZEN_TS - 600, soil_moisture=55.0, temperature=21.0)
    repo.add_sensor_reading(
        sensor_id=dry_probe, timestamp=FROZEN_TS - 300, soil_moisture=35.0, temperature=23.0, env_humidity=48.0
    )
    repo.add_sensor_reading(sensor_id=cactus_probe, timestamp=FROZEN_TS - 300, soil_moisture=15.0)
    repo.session.commit()

    tiles = {t["plant_id"]: t for t in _snapshots(repo, cid)["plants"]}

    assert tiles[fern]["soil"] == 35.0  # noqa: PLR2004 — the driest probe, not the mean
    assert tiles[fern]["band"] == (40.0, 60.0)
    assert (tiles[fern]["temperature"], tiles[fern]["humidity"]) == (23.0, 48.0)
    assert tiles[fern]["reading_age_seconds"] == 300  # noqa: PLR2004 — the newest raw reading
    assert tiles[fern]["sensor_count"] == 2  # noqa: PLR2004
    # The cactus is in its own band; the fern's would call it dry.
    assert tiles[cactus]["soil"] == 15.0  # noqa: PLR2004
    assert tiles[cactus]["band"] == (10.0, 20.0)


def test_out_of_range_soil_is_not_judged(repo: IrrigationRepository) -> None:
    """A physically impossible soil value is cleaned away: the tile shows the last plausible one."""
    cid = repo.add_cluster("Snapshot Cluster")
    pid = repo.add_plant(cluster_id=cid, species="Nephrolepis")
    sid = _sensor(repo, cid, "Fern", pid)
    repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - SECONDS_PER_HOUR, soil_moisture=42.0)
    repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 60, soil_moisture=140.0)
    repo.session.commit()

    (tile,) = _snapshots(repo, cid)["plants"]

    assert tile["soil"] == 42.0  # noqa: PLR2004
    assert tile["reading_age_seconds"] == 60  # noqa: PLR2004 — age is the raw row's


def test_stale_readings_are_not_current(repo: IrrigationRepository) -> None:
    """A silent sensor has no current soil and drops out of the conditions, but its age still shows."""
    cid = repo.add_cluster("Snapshot Cluster")
    pid = repo.add_plant(cluster_id=cid, species="Nephrolepis")
    bare = repo.add_plant(cluster_id=cid, species="Ficus")
    sid = _sensor(repo, cid, "Fern", pid)
    room = _sensor(repo, cid, "Room", None)
    old = FROZEN_TS - SENSOR_READING_STALE_SECONDS - 60
    repo.add_sensor_reading(sensor_id=sid, timestamp=old, soil_moisture=42.0, temperature=30.0)
    repo.add_sensor_reading(sensor_id=room, timestamp=FROZEN_TS - 60, temperature=20.0, env_humidity=50.0, light=900)
    repo.session.commit()

    result = _snapshots(repo, cid)
    tiles = {t["plant_id"]: t for t in result["plants"]}

    assert tiles[pid]["soil"] is None
    assert tiles[pid]["reading_age_seconds"] == FROZEN_TS - old
    assert tiles[bare]["sensor_count"] == 0
    assert tiles[bare]["soil"] is None
    assert result["conditions"] == {"temperature": 20.0, "humidity": 50.0, "light": 900.0}
