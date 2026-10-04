"""Follow-up gap tests: chronic-underwatering boundaries in ``learning/issues``.

Characterization (current behavior) for the two non-equivalent mutants that survived the
post-restructuring mutation run in ``_chronic_underwatering_alert``. Green before and after
that restructuring.
"""

from types import SimpleNamespace

import pytest

from greenhouse_core.learning import issues
from greenhouse_core.learning.issues import detect_issues
from greenhouse_core.learning.models import PlantProfile


class _Repo:
    def __init__(self, rows):
        self._rows = rows

    def get_sensors_in_cluster(self, cluster_id):
        return [SimpleNamespace(id=1, name="S1", plant_id=1)]

    def get_plants_in_cluster(self, cluster_id):
        return [SimpleNamespace(id=1, species="sp", category="cat")]

    def get_recent_readings(self, sensor_id, hours=24):
        return list(self._rows) if hours == 168 else []


class _PlantDb:
    def get_care_data(self, species=None, category=None):
        return {"soil_moisture_target": "40-60"}


def _row(ts, soil=None, humidity=None):
    return SimpleNamespace(
        timestamp=ts,
        temperature=None,
        soil_moisture=soil,
        env_humidity=humidity,
        light=None,
        battery_state=None,
        water_warning=None,
    )


def _detect(monkeypatch, rows):
    profile = PlantProfile(
        sensor_id=1,
        plant_id=1,
        sensor_name="S1",
        avg_absorption_per_minute=1.0,
        avg_drainage_per_hour=-1.0,
        response_count=5,
        min_delta=1.0,
        max_delta=5.0,
        efficiency_score=0.9,
    )
    monkeypatch.setattr(issues, "get_plant_profile", lambda db, sensor: profile)
    return detect_issues(_Repo(rows), _PlantDb(), 1)


@pytest.mark.parametrize(("peak", "fires"), [(40.0, False), (39.5, True)])
def test_chronic_underwatering_fires_only_strictly_below_target_min(monkeypatch, peak, fires):
    alerts = _detect(monkeypatch, [_row(1000 - i, soil=peak) for i in range(3)])
    assert [a.alert_type for a in alerts] == (["chronic_underwatering"] if fires else [])
    if fires:
        assert alerts[0].data == {"max_recent": peak, "target_min": 40.0}


def test_chronic_underwatering_peak_defaults_to_zero_when_no_moisture_values(monkeypatch):
    # Readings exist in the week (humidity only), so the check runs with max(..., default=0).
    alerts = _detect(monkeypatch, [_row(1000 - i, humidity=50.0) for i in range(3)])
    assert [a.alert_type for a in alerts] == ["chronic_underwatering"]
    assert alerts[0].data == {"max_recent": 0, "target_min": 40.0}
    assert "(0% peak vs 40% target)" in alerts[0].message
