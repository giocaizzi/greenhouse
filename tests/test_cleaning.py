"""Tests for the sensor-data cleaning layer (logic/cleaning.py)."""

import time
from dataclasses import dataclass

import pytest

from fake_data import FAKE_CLUSTER_NAME, FAKE_PLANT_SPECIES, FAKE_SENSOR_ID, FAKE_SENSOR_NAME
from greenhouse_core.constants import CLEANING_HAMPEL_MIN_READINGS
from greenhouse_core.logic.cleaning import CleanedReading, clean_readings
from greenhouse_core.logic.sensors import get_recent_sensor_data


@dataclass
class _Raw:
    """Minimal stand-in for a SensorReading row."""

    timestamp: int
    temperature: float | None = None
    soil_moisture: float | None = None
    env_humidity: float | None = None
    light: int | None = None
    battery_state: str | None = None
    water_warning: bool | None = None


def _series(values, *, field="soil_moisture", step=3600):
    """Build raw readings for one metric, oldest→newest, hourly by default."""
    base = 1_700_000_000
    return [_Raw(timestamp=base + i * step, **{field: v}) for i, v in enumerate(values)]


def _vals(cleaned, field="soil_moisture"):
    return [getattr(c, field) for c in cleaned]


def test_returns_cleaned_readings_sorted_oldest_first():
    raw = [_Raw(timestamp=300, soil_moisture=50.0), _Raw(timestamp=100, soil_moisture=40.0)]
    cleaned = clean_readings(raw)
    assert all(isinstance(c, CleanedReading) for c in cleaned)
    assert [c.timestamp for c in cleaned] == [100, 300]


def test_range_gate_drops_out_of_range_values():
    # Humidity of 250% is physically impossible -> dropped; valid neighbours kept.
    raw = _series([55.0, 250.0, 56.0, -5.0, 54.0], field="env_humidity")
    cleaned = clean_readings(raw)
    assert _vals(cleaned, "env_humidity") == [55.0, None, 56.0, None, 54.0]


def test_spike_is_rejected():
    # A lone spike to 5% inside a steady ~50% series is a classic probe glitch.
    values = [50.0, 51.0, 49.0, 50.0, 5.0, 51.0, 50.0, 49.0]
    cleaned = clean_readings(_series(values))
    out = _vals(cleaned)
    assert out[4] is None  # the spike
    assert out[:4] == values[:4]  # steady neighbours untouched
    assert out[5:] == values[5:]


def test_lone_zero_among_healthy_is_rejected_as_spike():
    # A disconnected-probe 0.0 sits in-range but is a spike against healthy soil.
    values = [45.0, 46.0, 44.0, 45.0, 0.0, 46.0, 45.0, 44.0]
    cleaned = clean_readings(_series(values))
    assert _vals(cleaned)[4] is None


def test_genuine_step_change_survives_flat_run():
    # Flat run then a real shift after irrigation: MAD≈0 must NOT shred the step.
    values = [40.0, 40.0, 40.0, 40.0, 40.0, 65.0, 65.0, 65.0, 65.0, 65.0]
    cleaned = clean_readings(_series(values))
    assert _vals(cleaned) == values


def test_genuine_dry_down_to_zero_survives():
    # A real, gradual dry-down ending at 0% (bone dry) must be preserved.
    values = [40.0, 32.0, 24.0, 16.0, 8.0, 0.0]
    cleaned = clean_readings(_series(values))
    assert _vals(cleaned) == values


def test_too_few_readings_skip_spike_filter():
    # Below the minimum, there's not enough context to call anything a spike.
    values = [50.0, 10.0, 50.0][: CLEANING_HAMPEL_MIN_READINGS - 2]
    cleaned = clean_readings(_series(values))
    assert _vals(cleaned) == values


def test_fields_cleaned_independently():
    # A soil spike must not discard the same reading's valid temperature.
    raw = [
        _Raw(timestamp=i, soil_moisture=s, temperature=t)
        for i, (s, t) in enumerate([(50, 22), (51, 22), (49, 23), (50, 22), (5, 23), (51, 22), (50, 22)])
    ]
    cleaned = clean_readings(raw)
    assert cleaned[4].soil_moisture is None  # spike dropped
    assert cleaned[4].temperature == 23.0  # temperature preserved


def test_passthrough_flags_preserved():
    raw = [_Raw(timestamp=i, soil_moisture=50.0, battery_state="low", water_warning=True) for i in range(6)]
    cleaned = clean_readings(raw)
    assert all(c.battery_state == "low" and c.water_warning is True for c in cleaned)


def test_empty_input():
    assert clean_readings([]) == []


@pytest.fixture
def cluster_with_sensor(tmp_db):
    cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
    plant_id = tmp_db.add_plant(
        cluster_id=cluster_id,
        species=FAKE_PLANT_SPECIES,
        category="tropical",
        water_needs="medium",
    )
    sensor_id = tmp_db.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id=FAKE_SENSOR_ID,
        name=FAKE_SENSOR_NAME,
        sensor_type="soil_moisture",
        config={},
        plant_id=plant_id,
    )
    return tmp_db, cluster_id, sensor_id


def test_snapshot_min_soil_ignores_spike(cluster_with_sensor):
    """The engine snapshot's min_soil_moisture must not be dragged by a spike."""
    db, cluster_id, sensor_id = cluster_with_sensor
    now = int(time.time())
    # Steady ~50% with a single 3% glitch one hour back.
    series = [50.0, 51.0, 49.0, 50.0, 3.0, 52.0, 50.0, 49.0]
    for i, v in enumerate(series):
        db.add_sensor_reading(sensor_id=sensor_id, timestamp=now - i * 3600, soil_moisture=v)
    db.session.commit()

    snapshot = get_recent_sensor_data(db, cluster_id, hours=24)
    # Without cleaning min would be 3.0; cleaned it tracks the real driest sample.
    assert snapshot.min_soil_moisture >= 49.0
    assert 49.0 <= snapshot.avg_soil_moisture <= 52.0
