"""Follow-up gap tests: chronic-underwatering boundaries in ``learning/issues``.

Characterization (current behavior) for the two non-equivalent mutants that survived the
post-restructuring mutation run in ``_chronic_underwatering_alert``. Green before and after
that restructuring.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from greenhouse_core.learning import issues
from greenhouse_core.learning.issues import detect_issues
from greenhouse_core.learning.models import Alert, PlantProfile

if TYPE_CHECKING:
    from greenhouse_core.plant_db import PlantDatabase
    from greenhouse_core.repository import IrrigationRepository

WEEK_HOURS = 168  # the chronic check's look-back


class _Repo:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def get_sensors_in_cluster(self, cluster_id: int) -> list[SimpleNamespace]:
        del cluster_id  # signature mirrors the real one; unused by this fake
        return [SimpleNamespace(id=1, name="S1", plant_id=1)]

    def get_plants_in_cluster(self, cluster_id: int) -> list[SimpleNamespace]:
        del cluster_id  # signature mirrors the real one; unused by this fake
        return [SimpleNamespace(id=1, species="sp", category="cat", soil_moisture_target=None)]

    def get_recent_readings(self, sensor_id: int, hours: int = 24) -> list[SimpleNamespace]:
        del sensor_id  # signature mirrors the real one; unused by this fake
        return list(self._rows) if hours == WEEK_HOURS else []

    def judged_since(self, sensor_id: int) -> int:
        del sensor_id  # no assignment history in this fake: no sensor was moved
        return 0

    def get_judged_readings(self, sensor_id: int, hours: int = 24) -> list[SimpleNamespace]:
        return self.get_recent_readings(sensor_id, hours)


class _PlantDb:
    def get_care_data(self, species: str | None = None, category: str | None = None) -> dict[str, str]:
        del species, category  # signature mirrors the real one; unused by this fake
        return {"soil_moisture_target": "40-60"}


def _row(ts: int, soil: float | None = None, humidity: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=ts,
        temperature=None,
        soil_moisture=soil,
        env_humidity=humidity,
        light=None,
        battery_state=None,
        water_warning=None,
    )


def _detect(monkeypatch: pytest.MonkeyPatch, rows: list[SimpleNamespace]) -> list[Alert]:
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
    monkeypatch.setattr(issues, "get_plant_profile", lambda _db, _sensor: profile)
    # Duck-typed fakes: detect_issues reads only the methods they implement.
    return detect_issues(cast("IrrigationRepository", _Repo(rows)), cast("PlantDatabase", _PlantDb()), 1)


@pytest.mark.parametrize(("peak", "fires"), [(40.0, False), (39.5, True)])
def test_chronic_underwatering_fires_only_strictly_below_target_min(
    monkeypatch: pytest.MonkeyPatch, peak: float, *, fires: bool
) -> None:
    """Chronic underwatering fires only when the weekly peak is strictly below the target minimum."""
    alerts = _detect(monkeypatch, [_row(1000 - i, soil=peak) for i in range(3)])
    assert [a.alert_type for a in alerts] == (["chronic_underwatering"] if fires else [])
    if fires:
        assert alerts[0].data == {"max_recent": peak, "target_min": 40.0}


def test_chronic_underwatering_peak_defaults_to_zero_when_no_moisture_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without moisture values the weekly peak defaults to 0, so the alert fires (pinned)."""
    # Readings exist in the week (humidity only), so the check runs with max(..., default=0).
    alerts = _detect(monkeypatch, [_row(1000 - i, humidity=50.0) for i in range(3)])
    assert [a.alert_type for a in alerts] == ["chronic_underwatering"]
    assert alerts[0].data == {"max_recent": 0, "target_min": 40.0}
    assert "(0% peak vs 40% target)" in alerts[0].message
