"""Characterization tests for repository / schema branches that no other test reaches.

Written before the WP3 restructuring tasks (T3.1–T3.5) so the shared helpers
(`_parse_json_config`, `_delete_by_id`, `_patch_fields`, `_patch_fields_hasattr_first`)
provably keep today's behavior. Everything here pins CURRENT behavior, odd or not.
"""

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from fake_data import FAKE_DEVICE_ID, FAKE_SENSOR_ID
from golden import FROZEN_TS
from greenhouse_core.models import IrrigationWindow, Irrigator, Plant, Sensor, VacationWindow
from greenhouse_core.schemas import IrrigatorResponse, SensorResponse

UNKNOWN_ID = 9999


def _count(repo, model) -> int:
    return repo.session.scalar(select(func.count()).select_from(model))


def _seed(repo):
    """One cluster with a plant, a sensor (no plant), an irrigator and an irrigation window."""
    cluster_id = repo.add_cluster("Gap Cluster", "Gap Location")
    plant_id = repo.add_plant(cluster_id, "Monstera deliciosa")
    sensor_id = repo.add_sensor(cluster_id, FAKE_SENSOR_ID, "Gap Sensor", "soil", {"k": 1})
    irrigator_id = repo.add_irrigator(cluster_id, FAKE_DEVICE_ID, "Gap Irrigator", "valve", {"pump": True})
    window = repo.add_irrigation_window(cluster_id, start_hour=6, end_hour=8, label="morning")
    return {
        "cluster": cluster_id,
        "plant": plant_id,
        "sensor": sensor_id,
        "irrigator": irrigator_id,
        "window": window.id,
    }


# ── T3.1: parse_config validators (schemas.py IrrigatorResponse / SensorResponse) ──

_RESPONSE_BASE = {"id": 1, "cluster_id": 2, "tuya_device_id": FAKE_DEVICE_ID, "name": "n", "type": "t"}
_RESPONSE_MODELS = [IrrigatorResponse, SensorResponse]


@pytest.mark.parametrize("model", _RESPONSE_MODELS, ids=lambda m: m.__name__)
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1, "b": [1, 2]}', {"a": 1, "b": [1, 2]}),
        ("{}", {}),
        ("null", None),
        ({"k": 1}, {"k": 1}),
        (None, None),
    ],
    ids=["json-object", "json-empty-object", "json-null", "dict-passthrough", "none-passthrough"],
)
def test_parse_config_decodes_json_strings_and_passes_other_values(model, raw, expected):
    assert model.model_validate({**_RESPONSE_BASE, "config": raw}).config == expected


@pytest.mark.parametrize("model", _RESPONSE_MODELS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("raw", ["not json", ""], ids=["garbage", "empty-string"])
def test_parse_config_invalid_json_is_a_value_error(model, raw):
    with pytest.raises(ValidationError) as exc:
        model.model_validate({**_RESPONSE_BASE, "config": raw})
    errors = exc.value.errors()
    assert [(e["type"], e["loc"], e["msg"]) for e in errors] == [
        ("value_error", ("config",), "Value error, Expecting value: line 1 column 1 (char 0)")
    ]


@pytest.mark.parametrize("model", _RESPONSE_MODELS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("raw", ["[1, 2]", '"x"', "3"], ids=["json-list", "json-string", "json-number"])
def test_parse_config_non_object_json_fails_dict_validation_current_behavior(model, raw):
    """Pins current behavior: a JSON string that decodes to a non-object fails dict validation."""
    with pytest.raises(ValidationError) as exc:
        model.model_validate({**_RESPONSE_BASE, "config": raw})
    assert [(e["type"], e["loc"]) for e in exc.value.errors()] == [("dict_type", ("config",))]


def test_parse_config_decodes_the_stored_orm_json_string(tmp_db):
    ids = _seed(tmp_db)
    irrigator = tmp_db.get_irrigator(ids["irrigator"])
    sensor = tmp_db.get_sensor(ids["sensor"])
    assert irrigator.config == '{"pump": true}'
    assert sensor.config == '{"k": 1}'
    assert IrrigatorResponse.model_validate(irrigator).config == {"pump": True}
    assert SensorResponse.model_validate(sensor).config == {"k": 1}


# ── T3.2: deleting an unknown id ──


@pytest.mark.parametrize(
    ("method", "model"),
    [
        ("delete_irrigation_window", IrrigationWindow),
        ("delete_sensor", Sensor),
        ("delete_irrigator", Irrigator),
        ("delete_vacation_window", VacationWindow),
    ],
)
def test_delete_unknown_id_returns_false_and_touches_nothing(tmp_db, frozen_clock, method, model):
    _seed(tmp_db)
    tmp_db.add_vacation_window(FROZEN_TS, FROZEN_TS + 3600)
    before = _count(tmp_db, model)
    assert before == 1
    result = getattr(tmp_db, method)(UNKNOWN_ID)
    assert result is False
    assert _count(tmp_db, model) == before


@pytest.mark.parametrize(
    ("method", "key", "model"),
    [
        ("delete_irrigation_window", "window", IrrigationWindow),
        ("delete_sensor", "sensor", Sensor),
        ("delete_irrigator", "irrigator", Irrigator),
    ],
)
def test_delete_known_id_returns_true_and_removes_the_row(tmp_db, method, key, model):
    ids = _seed(tmp_db)
    assert getattr(tmp_db, method)(ids[key]) is True
    assert _count(tmp_db, model) == 0


# ── T3.3: vacation / irrigation window PATCH ──


def test_update_vacation_window_ignores_unknown_keys_and_none(tmp_db, frozen_clock):
    window = tmp_db.add_vacation_window(FROZEN_TS, FROZEN_TS + 3600, contact_email="a@example.com", notes="keep")
    updated = tmp_db.update_vacation_window(window.id, bogus_field="x", notes=None, ends_at=FROZEN_TS + 7200)
    assert updated is window
    assert not hasattr(updated, "bogus_field")
    assert (updated.starts_at, updated.ends_at, updated.contact_email, updated.notes) == (
        FROZEN_TS,
        FROZEN_TS + 7200,
        "a@example.com",
        "keep",
    )


def test_update_vacation_window_unknown_id_returns_none(tmp_db):
    assert tmp_db.update_vacation_window(UNKNOWN_ID, notes="x") is None


def test_update_irrigation_window_ignores_unknown_keys_and_none(tmp_db):
    ids = _seed(tmp_db)
    updated = tmp_db.update_irrigation_window(ids["window"], bogus_field=1, label=None, end_hour=9)
    assert updated is not None
    assert updated.id == ids["window"]
    assert not hasattr(updated, "bogus_field")
    assert (updated.start_hour, updated.end_hour, updated.weekday_mask, updated.label) == (6, 9, 127, "morning")


def test_update_irrigation_window_unknown_id_returns_none(tmp_db):
    _seed(tmp_db)
    assert tmp_db.update_irrigation_window(UNKNOWN_ID, end_hour=9) is None


# ── T3.4: sensor / irrigator PATCH ──


def test_update_sensor_unknown_id_returns_none(tmp_db):
    _seed(tmp_db)
    assert tmp_db.update_sensor(UNKNOWN_ID, name="x") is None


def test_update_sensor_skips_none_values(tmp_db):
    ids = _seed(tmp_db)
    updated = tmp_db.update_sensor(ids["sensor"], name=None, type=None, config=None)
    assert (updated.name, updated.type, updated.config) == ("Gap Sensor", "soil", '{"k": 1}')


def test_update_sensor_json_encodes_dict_config(tmp_db):
    ids = _seed(tmp_db)
    updated = tmp_db.update_sensor(ids["sensor"], config={"b": 1, "a": [1, 2], "c": None})
    assert updated.config == '{"b": 1, "a": [1, 2], "c": null}'
    assert updated.config == json.dumps({"b": 1, "a": [1, 2], "c": None})


def test_update_sensor_stores_non_dict_config_verbatim_current_behavior(tmp_db):
    """Pins current behavior: a non-dict ``config`` skips JSON encoding and is set as-is."""
    ids = _seed(tmp_db)
    updated = tmp_db.update_sensor(ids["sensor"], config='{"raw": 1}')
    assert updated.config == '{"raw": 1}'


def test_update_sensor_ignores_unknown_keys(tmp_db):
    ids = _seed(tmp_db)
    updated = tmp_db.update_sensor(ids["sensor"], bogus_field="x", name="Renamed")
    assert not hasattr(updated, "bogus_field")
    assert (updated.name, updated.type, updated.config, updated.plant_id) == ("Renamed", "soil", '{"k": 1}', None)


def test_update_irrigator_unknown_id_returns_none(tmp_db):
    _seed(tmp_db)
    assert tmp_db.update_irrigator(UNKNOWN_ID, name="x") is None


def test_update_irrigator_ignores_unknown_keys_and_none(tmp_db):
    ids = _seed(tmp_db)
    updated = tmp_db.update_irrigator(ids["irrigator"], bogus_field="x", type=None, name="Renamed")
    assert not hasattr(updated, "bogus_field")
    assert (updated.name, updated.type, updated.config) == ("Renamed", "valve", '{"pump": true}')


def test_update_irrigator_json_encodes_dict_config(tmp_db):
    ids = _seed(tmp_db)
    updated = tmp_db.update_irrigator(ids["irrigator"], config={"z": 1, "a": "b"})
    assert updated.config == '{"z": 1, "a": "b"}'


# ── T3.5: plant PATCH ──


def test_update_plant_unknown_id_returns_none(tmp_db):
    _seed(tmp_db)
    assert tmp_db.update_plant(UNKNOWN_ID, notes="x") is None
    assert _count(tmp_db, Plant) == 1


def test_update_plant_ignores_unknown_keys_and_none_current_behavior(tmp_db):
    """Pins current behavior (B-15): ``None`` cannot clear a field; unknown keys are ignored."""
    ids = _seed(tmp_db)
    tmp_db.update_plant(ids["plant"], notes="first")
    updated = tmp_db.update_plant(ids["plant"], bogus_field="x", notes=None, category="tropical")
    assert not hasattr(updated, "bogus_field")
    assert (updated.species, updated.notes, updated.category) == ("Monstera deliciosa", "first", "tropical")


# ── T3.7: get_vacation_window ──


def test_get_vacation_window_found_and_not_found(tmp_db, frozen_clock):
    window = tmp_db.add_vacation_window(FROZEN_TS, FROZEN_TS + 3600, notes="trip")
    assert tmp_db.get_vacation_window(window.id) is window
    assert tmp_db.get_vacation_window(UNKNOWN_ID) is None
