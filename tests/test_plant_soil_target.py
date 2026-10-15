"""A plant's own ``soil_moisture_target`` (issue #182): strict write-time validation and care-data precedence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from greenhouse_core.logic.decision import TriggerCode
from greenhouse_core.logic.engine import IrrigationLogic
from greenhouse_core.logic.plant_needs import moisture_target_range, normalize_moisture_target, parse_moisture_target
from greenhouse_core.models import Plant
from greenhouse_core.plant_db import PlantDatabase, plant_care_data

if TYPE_CHECKING:
    from greenhouse_core.repository import IrrigationRepository

KNOWN_SPECIES = "Ficus benjamina"  # 25-40 in the bundled plant database


@pytest.mark.parametrize(
    ("target", "stored"),
    [("30-50", "30-50"), (" 60 - 30 ", "30-60"), ("35.5-55.0", "35.5-55"), ("0-100", "0-100"), ("40-60-80", "40-60")],
)
def test_valid_targets_are_normalised(target: str, stored: str) -> None:
    """Accepted targets are stored as ordered ``"low-high"``."""
    assert normalize_moisture_target(target) == stored


@pytest.mark.parametrize("target", ["50", "", "a-b", "-5-50", "20-120", "nan-50", "inf-50", "-"])
def test_targets_the_parser_would_default_are_refused(target: str) -> None:
    """Anything ``parse_moisture_target`` would silently replace with 45-65 is a ValueError on write."""
    with pytest.raises(ValueError, match="soil_moisture_target"):
        normalize_moisture_target(target)


@given(st.text(max_size=12))
def test_a_normalised_target_parses_to_the_same_band(target: str) -> None:
    """Whatever the validator accepts, the lenient parser reads as the same band."""
    try:
        stored = normalize_moisture_target(target)
    except ValueError:
        return
    assert parse_moisture_target(stored) == parse_moisture_target(target)


def test_the_plant_target_wins_over_the_species() -> None:
    """The plant's own band is the most specific layer; without it the species band applies."""
    plant_db = PlantDatabase()
    plant = Plant(species=KNOWN_SPECIES, soil_moisture_target="50-70")
    assert moisture_target_range(plant_care_data(plant_db, plant)) == (50.0, 70.0)
    plant.soil_moisture_target = None
    assert moisture_target_range(plant_care_data(plant_db, plant)) == (25.0, 40.0)


def test_the_engine_judges_the_linked_sensor_on_the_plant_target(tmp_db: IrrigationRepository) -> None:
    """45 % is wet for the species (25-40) but dry against the plant's own 50-70, and the trail says so."""
    cid = tmp_db.add_cluster("Own band")
    pid = tmp_db.add_plant(cluster_id=cid, species=KNOWN_SPECIES, soil_moisture_target="50-70")
    sid = tmp_db.add_sensor(cid, "fake_tuya_device_aabbccdd", "Soil", "tuya.tr301z", {}, plant_id=pid)
    tmp_db.add_sensor_reading(sensor_id=sid, soil_moisture=45.0)
    decision = IrrigationLogic(tmp_db, PlantDatabase()).decide_for_cluster(cid)
    assert decision is not None
    dry = [r for r in decision.reasons if r.code == TriggerCode.SENSOR_DRY]
    assert len(dry) == 1
    assert "< 50.0%, plant band" in dry[0].message


def test_no_plant_gets_the_defaults() -> None:
    """A sensor with no linked plant is judged on the plain defaults."""
    assert moisture_target_range(plant_care_data(PlantDatabase(), None)) == (45.0, 65.0)
