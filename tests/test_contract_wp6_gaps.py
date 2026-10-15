"""Coverage / mutation gap tests (characterization, current behavior).

Pins boundaries in ``logic/stress.py`` that only the golden grid guarded (mutation
survivors) and branches of ``learning/issues.py`` that no test reached,
written before those functions were restructured. Each test was proven to fail under a
temporary in-place mutation of the production line it pins.
"""

import time
from collections.abc import Iterable, Mapping
from types import SimpleNamespace
from typing import cast

import pytest

from fake_data import FAKE_CLUSTER_NAME, FAKE_DEVICE_ID, FAKE_IRRIGATOR_NAME
from greenhouse_core.learning import issues
from greenhouse_core.learning.issues import detect_conflicts, detect_issues
from greenhouse_core.learning.models import Alert, PlantProfile
from greenhouse_core.logic.decision import PerSensorSnapshot, SensorSnapshot, StressIndicators, Trends
from greenhouse_core.logic.stress import detect_stress_conditions
from greenhouse_core.logic.trends import analyze_historical_trends
from greenhouse_core.models import EventAction, TriggeredBy
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.stats import get_irrigation_stats

STATS_EVENTS_LOGGED = 2  # one start with a duration, one stop without
STATS_START_MINUTES = 3


class _Repo:
    def __init__(self, n_plants: int = 1) -> None:
        self._plants = [SimpleNamespace(id=i + 1, species=f"s{i}", category="c") for i in range(n_plants)]

    def get_plants_in_cluster(self, cluster_id: int) -> list[SimpleNamespace]:
        del cluster_id  # signature mirrors the real one; unused by this fake
        return self._plants


class _PlantDb:
    def __init__(self, care: Mapping[str, object]) -> None:
        self._care = care

    def get_care_data(self, species: str | None = None, category: str | None = None) -> dict[str, object]:
        del species, category  # signature mirrors the real one; unused by this fake
        return dict(self._care)


def _plant_db(care: Mapping[str, object]) -> PlantDatabase:
    """Hand the care-data fake to the code under test, which reads only ``get_care_data``."""
    return cast("PlantDatabase", _PlantDb(care))


def _stress(care: Mapping[str, object], snapshot: SensorSnapshot, trends: Trends | None = None) -> StressIndicators:
    return detect_stress_conditions(_as_repo(_Repo()), _plant_db(care), 1, snapshot, trends or Trends())


# ── low_env_humidity: deficit of exactly 20 points ────────────────────────────


@pytest.mark.parametrize(("humidity", "fires"), [(39.5, True), (40.0, False), (40.5, False)])
def test_low_env_humidity_fires_strictly_more_than_20_points_below_ideal_min(humidity: float, *, fires: bool) -> None:
    """Low-humidity stress fires only strictly more than 20 points below the ideal minimum."""
    care = {"ideal_humidity_min": 60, "ideal_humidity_max": 80}
    stress = _stress(care, SensorSnapshot(avg_env_humidity=humidity))
    assert (stress.low_env_humidity is not None) is fires


# ── low_light: strictly below 0.4 x the seasonal minimum ──────────────────────


@pytest.mark.parametrize(("light", "fires"), [(399.0, True), (400.0, False)])
def test_low_light_fires_strictly_below_forty_percent_of_seasonal_min(
    monkeypatch: pytest.MonkeyPatch, light: float, *, fires: bool
) -> None:
    """Low-light stress fires only strictly below 40 % of the seasonal minimum."""
    monkeypatch.setattr("greenhouse_core.utils.seasonal_light_factor", lambda _month=None: 1.0)
    stress = _stress({"ideal_light_lux_min": 1000}, SensorSnapshot(avg_light=light))
    assert (stress.low_light is not None) is fires
    if fires:
        assert stress.low_light == (
            "insufficient light (399 lux vs seasonal min 1000) — reduced transpiration and growth"
        )


# ── water_stress: low band and the steep-decline delta ────────────────────────


@pytest.mark.parametrize(
    ("soil", "delta", "fires"),
    [
        (39.9, -10.5, True),
        (40.0, -10.5, False),  # SOIL_MOISTURE_LOW itself is not "low"
        (35.0, -10.0, False),  # a delta of exactly -10 is not steep
        (35.0, -9.5, False),
        (35.0, -10.5, True),
        (35.0, -11.0, True),
    ],
)
def test_water_stress_low_and_steep_decline_boundaries(soil: float, delta: float, *, fires: bool) -> None:
    """Water stress fires at the exact low-moisture and steep-decline boundaries."""
    trends = Trends(soil_moisture_trend="declining", soil_moisture_delta=delta)
    current = [PerSensorSnapshot(sensor_id=1, name="Soil", latest_soil_moisture=soil)]
    stress = _stress({}, SensorSnapshot(avg_soil_moisture=soil, per_sensor=current), trends)
    assert (stress.water_stress is not None) is fires
    if fires:
        assert stress.water_stress == f"low ({soil:.0f}%) + steep decline ({delta:.0f}%)"


# ── over_watering: strictly above SOIL_MOISTURE_SATURATED ─────────────────────


@pytest.mark.parametrize(("soil", "fires"), [(70.0, False), (70.1, True)])
def test_over_watering_fires_strictly_above_saturated(soil: float, *, fires: bool) -> None:
    """Over-watering stress fires only when a current reading is strictly above the saturation level."""
    trends = Trends(irrigation_frequency_high=True)
    current = [PerSensorSnapshot(sensor_id=1, name="Soil", latest_soil_moisture=soil)]
    stress = _stress({}, SensorSnapshot(avg_soil_moisture=soil, per_sensor=current), trends)
    assert (stress.over_watering is not None) is fires


@pytest.mark.parametrize(("avg", "latest", "fires"), [(85.0, 55.0, False), (55.0, 85.0, True), (85.0, None, False)])
def test_over_watering_judges_current_readings_not_the_average(
    avg: float, latest: float | None, *, fires: bool
) -> None:
    """Issue #121: a drained sensor ends the skip at once; a stale one (no current reading) neither fires it."""
    trends = Trends(irrigation_frequency_high=True)
    current = [PerSensorSnapshot(sensor_id=1, name="Soil", latest_soil_moisture=latest)]
    stress = _stress({}, SensorSnapshot(avg_soil_moisture=avg, per_sensor=current), trends)
    assert (stress.over_watering is not None) is fires


def test_over_watering_defers_a_mixed_cluster_to_the_soil_rule() -> None:
    """A saturated sensor beside one below its band is no terminal skip: the conflict burst decides."""
    trends = Trends(irrigation_frequency_high=True)
    current = [
        PerSensorSnapshot(sensor_id=1, name="Wet", latest_soil_moisture=85.0),
        PerSensorSnapshot(sensor_id=2, name="Dry", latest_soil_moisture=20.0),
    ]
    stress = _stress({}, SensorSnapshot(avg_soil_moisture=52.5, per_sensor=current), trends)
    assert stress.over_watering is None


# ── learning/issues: fakes ────────────────────────────────────────────────────


def _row(
    ts: int, soil: float | None = None, humidity: float | None = None, light: int | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=ts,
        temperature=None,
        soil_moisture=soil,
        env_humidity=humidity,
        light=light,
        battery_state=None,
        water_warning=None,
    )


# Readings a fake serves, keyed by (sensor_id, hours).
_Readings = dict[tuple[int, int], list[SimpleNamespace]]


class _LearningRepo:
    """Sensors/plants of one cluster; readings keyed by (sensor_id, hours)."""

    def __init__(
        self,
        sensors: Iterable[SimpleNamespace],
        plants: Iterable[SimpleNamespace] = (),
        readings: _Readings | None = None,
    ) -> None:
        self._sensors = list(sensors)
        self._plants = list(plants)
        self._readings = readings or {}
        self.calls: list[tuple[int, int]] = []

    def get_sensors_in_cluster(self, cluster_id: int) -> list[SimpleNamespace]:
        del cluster_id  # signature mirrors the real one; unused by this fake
        return self._sensors

    def get_plants_in_cluster(self, cluster_id: int) -> list[SimpleNamespace]:
        del cluster_id  # signature mirrors the real one; unused by this fake
        return self._plants

    def get_recent_readings(self, sensor_id: int, hours: int = 24) -> list[SimpleNamespace]:
        self.calls.append((sensor_id, hours))
        return list(self._readings.get((sensor_id, hours), []))

    def get_preferences(self) -> None:
        return None  # no preferences row: the learning light season reads the UTC month


def _sensor(sid: int, plant_id: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=sid, name=f"S{sid}", plant_id=plant_id)


def _plant(pid: int, species: str = "sp") -> SimpleNamespace:
    return SimpleNamespace(id=pid, species=f"{species}{pid}", category="cat")


def _profile(
    sid: int, *, absorption: float = 1.0, drainage: float = -1.0, responses: int = 5, efficiency: float = 0.9
) -> PlantProfile:
    return PlantProfile(
        sensor_id=sid,
        plant_id=None,
        sensor_name=f"S{sid}",
        avg_absorption_per_minute=absorption,
        avg_drainage_per_hour=drainage,
        response_count=responses,
        min_delta=1.0,
        max_delta=5.0,
        efficiency_score=efficiency,
    )


def _soil(value: float, n: int = 3) -> list[SimpleNamespace]:
    return [_row(1000 - i, soil=value) for i in range(n)]


# ── detect_issues: chronic-underwatering branches ─────────────────────────────


def _issues(
    monkeypatch: pytest.MonkeyPatch,
    sensors: list[SimpleNamespace],
    plants: list[SimpleNamespace],
    care: Mapping[str, object],
    readings: _Readings,
) -> list[Alert]:
    profiles = {s.id: _profile(s.id) for s in sensors}
    monkeypatch.setattr(issues, "get_plant_profile", lambda _db, sensor: profiles.get(sensor.id))
    return detect_issues(_as_repo(_LearningRepo(sensors, plants, readings)), _plant_db(care), 1)


def test_detect_issues_skips_chronic_check_for_sensor_without_plant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chronic-underwatering check skips sensors that have no plant."""
    sensor = _sensor(1, plant_id=None)
    alerts = _issues(monkeypatch, [sensor], [], {}, {(1, 168): _soil(10.0)})
    assert alerts == []


@pytest.mark.parametrize("target", ["n/a", "", "50"])  # "50": no band → default min (D10; was read as 50)
def test_detect_issues_chronic_unparsable_target_falls_back_to_45(monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    """An unparsable soil target falls back to a 45 % minimum for the chronic check."""
    sensor = _sensor(1, plant_id=1)
    alerts = _issues(monkeypatch, [sensor], [_plant(1)], {"soil_moisture_target": target}, {(1, 168): _soil(30.0)})
    assert [a.alert_type for a in alerts] == ["chronic_underwatering"]
    assert alerts[0].data == {"max_recent": 30.0, "target_min": 45.0}


def test_detect_issues_chronic_needs_readings_in_the_last_week(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without readings in the last week the chronic check raises nothing."""
    sensor = _sensor(1, plant_id=1)
    alerts = _issues(monkeypatch, [sensor], [_plant(1)], {"soil_moisture_target": "60-80"}, {})
    assert alerts == []


# ── detect_conflicts ──────────────────────────────────────────────────────────


def test_detect_conflicts_needs_two_sensors_with_moisture_and_then_skips_every_check() -> None:
    """With fewer than two moisture sensors every conflict check is skipped (pinned quirk)."""
    # Only S1 has recent moisture. The low-light and low-humidity checks would fire
    # for S1's plant, but the early return skips them too (quirk, preserved).
    sensors = [_sensor(1, plant_id=1), _sensor(2, plant_id=2)]
    readings = {
        (1, 6): _soil(30.0),
        (2, 6): [_row(1000, humidity=50.0)],
        (1, 168): [_row(1000 - i, light=20) for i in range(6)],
        (1, 48): [_row(1000 - i, humidity=10.0) for i in range(6)],
    }
    care = {"ideal_light_lux_min": 5000, "ideal_humidity_min": 60}
    repo = _LearningRepo(sensors, [_plant(1), _plant(2)], readings)
    alerts = detect_conflicts(_as_repo(repo), _plant_db(care), 1, {1: _profile(1), 2: _profile(2)}, {})
    assert alerts == []
    assert repo.calls == [(1, 6), (2, 6)]


def test_detect_conflicts_skips_sensor_without_recent_moisture_and_defaults_unknown_bands() -> None:
    """Sensors without moisture are skipped and unknown or half-parsed targets use the default band."""
    # S1: no plant → default band 45-65, dry at 30. S2: target "55-x" fails on the max →
    # BOTH bounds stay 45/65 (tuple assignment never happens), wet at 71. S3: no moisture.
    sensors = [_sensor(1), _sensor(2, plant_id=2), _sensor(3, plant_id=3)]
    readings = {(1, 6): _soil(30.0), (2, 6): _soil(71.0), (3, 6): [_row(1000, humidity=40.0)]}
    plant_care = {2: {"soil_moisture_target": "55-x"}, 3: {"soil_moisture_target": "10-20"}}
    repo = _LearningRepo(sensors, [_plant(2), _plant(3)], readings)
    profiles = {1: _profile(1), 2: _profile(2), 3: _profile(3)}
    alerts = detect_conflicts(_as_repo(repo), _plant_db({}), 1, profiles, plant_care)
    assert [a.alert_type for a in alerts] == ["unresolvable_conflict"]
    assert alerts[0].sensor_name == "S1 vs S2"
    assert alerts[0].data == {
        "dry_sensor": "S1",
        "dry_moisture": 30.0,
        "wet_sensor": "S2",
        "wet_moisture": 71.0,
        "needed_minutes": 15.0,
        "projected_wet": 86.0,
    }
    assert "max 65%" in alerts[0].message


@pytest.mark.parametrize("target", ["wet", "50"])
def test_detect_conflicts_unparsable_target_uses_default_band(target: str) -> None:
    """An unparsable target uses the default 45-65 % band."""
    sensors = [_sensor(1, plant_id=1), _sensor(2)]
    readings = {(1, 6): _soil(39.0), (2, 6): _soil(71.0)}
    repo = _LearningRepo(sensors, [], readings)
    alerts = detect_conflicts(
        _as_repo(repo), _plant_db({}), 1, {1: _profile(1), 2: _profile(2)}, {1: {"soil_moisture_target": target}}
    )
    # 39 < 45 - 5 → dry against the default minimum 45 (needs 6 min → wet 77, below 85).
    assert alerts == []


@pytest.mark.parametrize(
    ("profiles", "wet", "expected"),
    [
        ({2: _profile(2)}, 71.0, []),  # dry sensor has no profile
        ({1: _profile(1, absorption=0.0), 2: _profile(2)}, 71.0, []),  # dry sensor absorbs nothing
        ({1: _profile(1)}, 71.0, []),  # wet sensor has no profile
        ({1: _profile(1), 2: _profile(2)}, 70.0, []),  # projected exactly 85 → not over
        ({1: _profile(1), 2: _profile(2)}, 70.5, ["S1 vs S2"]),
    ],
)
def test_detect_conflicts_profile_guards_and_over_water_line(
    profiles: dict[int, PlantProfile], wet: float, expected: list[str]
) -> None:
    """Conflict detection needs both profiles and pins the over-watering line."""
    sensors = [_sensor(1), _sensor(2)]
    repo = _LearningRepo(sensors, [], {(1, 6): _soil(30.0), (2, 6): _soil(wet)})
    alerts = detect_conflicts(_as_repo(repo), _plant_db({}), 1, profiles, {})
    assert [a.sensor_name for a in alerts] == expected


def test_detect_conflicts_light_and_humidity_need_care_fields_and_enough_samples() -> None:
    """Light and humidity conflicts need the care field and at least five samples."""
    # Plant 1 has only a humidity need (light check skipped) and just 4 humidity
    # samples (< 5 → skipped); plant 2 has a zero light need and no humidity need.
    sensors = [_sensor(1, plant_id=1), _sensor(2, plant_id=2)]
    readings = {
        (1, 6): _soil(50.0),
        (2, 6): _soil(50.0),
        (1, 48): [_row(1000 - i, humidity=10.0) for i in range(4)],
    }

    class _Care:
        def get_care_data(self, species: str | None = None, category: str | None = None) -> dict[str, int]:
            del category  # signature mirrors the real one; unused by this fake
            return {"ideal_humidity_min": 60} if species == "sp1" else {"ideal_light_lux_min": 0}

    repo = _LearningRepo(sensors, [_plant(1), _plant(2)], readings)
    alerts = detect_conflicts(_as_repo(repo), cast("PlantDatabase", _Care()), 1, {}, {})
    assert alerts == []
    assert repo.calls == [(1, 6), (2, 6), (1, 48)]


# ── stats: events without a duration ──────────────────────────────────────────


def test_get_irrigation_stats_counts_events_without_duration_but_not_as_irrigations(
    tmp_db: IrrigationRepository,
) -> None:
    """Events without a duration are counted as events but not as irrigations."""
    cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
    irrigator_id = tmp_db.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id=FAKE_DEVICE_ID,
        name=FAKE_IRRIGATOR_NAME,
        irrigator_type="rainpoint.ik10pw",
        config={},
    )
    now = int(time.time())
    tmp_db.add_irrigation_event(
        irrigator_id=irrigator_id,
        action=EventAction.START,
        triggered_by=TriggeredBy.AUTO,
        duration_minutes=3,
        timestamp=now - 60,
    )
    tmp_db.add_irrigation_event(
        irrigator_id=irrigator_id, action=EventAction.STOP, triggered_by=TriggeredBy.MANUAL, timestamp=now - 30
    )
    stats = get_irrigation_stats(tmp_db, cluster_id, days=1)
    assert "error" not in stats
    assert stats["total_events"] == STATS_EVENTS_LOGGED
    assert dict(stats["events_by_type"]) == {"start": 1, "stop": 1}
    assert dict(stats["events_by_trigger"]) == {"auto": 1, "manual": 1}
    assert [i["duration_minutes"] for i in stats["irrigations"]] == [3]
    assert stats["total_duration_minutes"] == STATS_START_MINUTES


# ── logic/trends: exact thresholds and lookback windows ───────────────────────


class _TrendRepo:
    def __init__(
        self, rows: list[SimpleNamespace], events: Iterable[SimpleNamespace] = (), *, irrigator: bool = True
    ) -> None:
        self._rows = rows
        self._events = list(events)
        self._irrigator = SimpleNamespace(id=7) if irrigator else None
        self.calls: list[tuple[str, int, int]] = []

    def get_sensors_in_cluster(self, cluster_id: int) -> list[SimpleNamespace]:
        del cluster_id  # signature mirrors the real one; unused by this fake
        return [SimpleNamespace(id=1)]

    def get_recent_readings(self, sensor_id: int, hours: int = 24) -> list[SimpleNamespace]:
        self.calls.append(("readings", sensor_id, hours))
        return list(self._rows)

    def get_irrigator_for_cluster(self, cluster_id: int) -> SimpleNamespace | None:
        del cluster_id  # signature mirrors the real one; unused by this fake
        return self._irrigator

    def get_recent_events(self, irrigator_id: int, hours: int = 24) -> list[SimpleNamespace]:
        self.calls.append(("events", irrigator_id, hours))
        return list(self._events)


def _as_repo(repo: _Repo | _LearningRepo | _TrendRepo) -> IrrigationRepository:
    """Hand a repository fake to the code under test, which calls only the read methods it implements."""
    return cast("IrrigationRepository", repo)


def _trend_rows(first: float, second: float, field: str) -> list[SimpleNamespace]:
    values = [first, first, second, second]
    rows: list[SimpleNamespace] = []
    for i, value in enumerate(values):
        row = _row(1000 + i)
        setattr(row, field, value)
        rows.append(row)
    return rows


@pytest.mark.parametrize(
    ("second", "label"), [(45.0, "stable"), (35.0, "stable"), (45.5, "rising"), (34.5, "declining")]
)
def test_moisture_trend_threshold_is_strict(second: float, label: str) -> None:
    """The moisture trend label changes only strictly past its threshold."""
    trends = analyze_historical_trends(_as_repo(_TrendRepo(_trend_rows(40.0, second, "soil_moisture"))), 1)
    assert trends.soil_moisture_trend == label
    assert trends.soil_moisture_delta == second - 40.0


@pytest.mark.parametrize(("second", "label"), [(22.0, "stable"), (18.0, "stable"), (22.5, "rising"), (17.5, "falling")])
def test_temperature_trend_threshold_is_strict(second: float, label: str) -> None:
    """The temperature trend label changes only strictly past its threshold."""
    trends = analyze_historical_trends(_as_repo(_TrendRepo(_trend_rows(20.0, second, "temperature"))), 1)
    assert trends.temperature_trend == label


def test_trend_lookback_windows_are_48_hours_and_7_days() -> None:
    """Trends read 48 h of readings and 7 days of events."""
    repo = _TrendRepo([])
    analyze_historical_trends(_as_repo(repo), 1)
    assert repo.calls == [("readings", 1, 48), ("events", 7, 168)]


def _starts(n: int, minutes: int = 1) -> list[SimpleNamespace]:
    return [SimpleNamespace(action="start", duration_minutes=minutes) for _ in range(n)]


@pytest.mark.parametrize(
    ("events", "low", "high"),
    [
        (_starts(1, 1), True, False),  # one short run in 7 days → sparse
        (_starts(1, 2), False, False),  # average of exactly 2 min is not "short"
        (_starts(1, 5), False, False),  # sparse but long enough → both conditions needed
        (_starts(7, 1), False, False),  # exactly 1/day is not "< 1/day"
        (_starts(6, 1), True, False),
        (_starts(21, 1), False, False),  # exactly 3/day is not "> 3/day"
        (_starts(22, 1), False, True),
        ([], False, False),
    ],
)
def test_cadence_flags_boundaries(events: list[SimpleNamespace], *, low: bool, high: bool) -> None:
    """The low / high irrigation-cadence flags flip at their exact boundaries."""
    trends = analyze_historical_trends(_as_repo(_TrendRepo([], events)), 1)
    assert (trends.irrigation_frequency_low, trends.irrigation_frequency_high) == (low, high)


# ── detect_issues: per-sensor alert conditions (mutation survivors) ───────────


def _issues_with(
    monkeypatch: pytest.MonkeyPatch,
    profile: PlantProfile,
    readings: _Readings,
    care: Mapping[str, object] | None = None,
    plant_id: int = 1,
) -> tuple[list[Alert], _LearningRepo]:
    sensor = _sensor(1, plant_id=plant_id)
    monkeypatch.setattr(issues, "get_plant_profile", lambda _db, _s: profile)
    monkeypatch.setattr(issues, "seasonal_light_factor", lambda _month=None: 1.0)
    repo = _LearningRepo([sensor], [_plant(1)], readings)
    plant_db = _plant_db(care if care is not None else {"soil_moisture_target": "20-40"})
    return detect_issues(_as_repo(repo), plant_db, 1), repo


@pytest.mark.parametrize(
    ("efficiency", "absorption", "blocked"),
    [(0.2, 0.4, True), (0.2, 0.5, False), (0.3, 0.4, False), (0.9, 0.1, False)],
)
def test_blocked_drip_needs_low_efficiency_and_low_absorption(
    monkeypatch: pytest.MonkeyPatch, efficiency: float, absorption: float, *, blocked: bool
) -> None:
    """A blocked-drip issue needs both low efficiency and low absorption."""
    profile = _profile(1, absorption=absorption, efficiency=efficiency)
    alerts, _ = _issues_with(monkeypatch, profile, {(1, 168): _soil(50.0)})
    assert ("blocked_drip" in [a.alert_type for a in alerts]) is blocked


@pytest.mark.parametrize(("lux", "alert_type"), [(800, "rapid_drainage"), (801, "light_accelerated_drainage")])
def test_rapid_drainage_is_light_driven_only_strictly_above_the_bright_threshold(
    monkeypatch: pytest.MonkeyPatch, lux: int, alert_type: str
) -> None:
    """Rapid drainage is attributed to light only strictly above the bright threshold."""
    profile = _profile(1, drainage=-6.0)
    readings = {(1, 48): [_row(1000 - i, light=lux) for i in range(3)], (1, 168): _soil(50.0)}
    alerts, repo = _issues_with(monkeypatch, profile, readings)
    assert [a.alert_type for a in alerts] == [alert_type]
    assert repo.calls == [(1, 48), (1, 168)]


# ── detect_conflicts: bands, latest samples, light / humidity thresholds ──────


def test_detect_conflicts_averages_only_the_latest_three_moisture_samples() -> None:
    """Conflict detection averages only the latest three moisture samples."""
    # DESC rows: 30, 30, 36, 0 → latest three average 32 → dry; needs 13 min → wet 71 + 13 = 84 (no alert).
    # With only the latest two (30) the need would be 15 min → 86 → alert.
    sensors = [_sensor(1), _sensor(2)]
    rows_1 = [_row(1000, soil=30.0), _row(999, soil=30.0), _row(998, soil=36.0), _row(997, soil=0.0)]
    repo = _LearningRepo(sensors, [], {(1, 6): rows_1, (2, 6): _soil(71.0)})
    assert detect_conflicts(_as_repo(repo), _plant_db({}), 1, {1: _profile(1), 2: _profile(2)}, {}) == []


@pytest.mark.parametrize(
    ("dry", "wet", "expected"),
    [
        (40.0, 80.0, []),  # exactly target_min - 5 is not dry
        (39.5, 80.0, ["S1 vs S2"]),
        (30.0, 65.0, []),  # exactly target_max is not wet
        (30.0, 65.5, ["S1 vs S2"]),
    ],
)
def test_detect_conflicts_dry_and_wet_band_edges(dry: float, wet: float, expected: list[str]) -> None:
    """Dry / wet conflicts fire at the exact band edges."""
    sensors = [_sensor(1, plant_id=9), _sensor(2)]  # plant 9 has no care entry → default band
    repo = _LearningRepo(sensors, [], {(1, 6): _soil(dry), (2, 6): _soil(wet)})
    profiles = {1: _profile(1, absorption=0.1), 2: _profile(2, absorption=10.0)}
    alerts = detect_conflicts(_as_repo(repo), _plant_db({}), 1, profiles, {})
    assert [a.sensor_name for a in alerts] == expected


def _env_repo(lux_rows: Iterable[SimpleNamespace] = (), hum_rows: Iterable[SimpleNamespace] = ()) -> _LearningRepo:
    sensors = [_sensor(1, plant_id=1), _sensor(2)]
    readings = {(1, 6): _soil(50.0), (2, 6): _soil(50.0), (1, 168): list(lux_rows), (1, 48): list(hum_rows)}
    return _LearningRepo(sensors, [_plant(1)], readings)


@pytest.mark.parametrize(("n", "lux", "fires"), [(5, 499, True), (4, 499, False), (5, 500, False)])
def test_detect_conflicts_low_light_needs_five_daytime_samples_strictly_below_half(
    monkeypatch: pytest.MonkeyPatch, n: int, lux: int, *, fires: bool
) -> None:
    """A low-light conflict needs five daytime samples strictly below half the need."""
    monkeypatch.setattr("greenhouse_core.utils.seasonal_light_factor", lambda _month=None: 1.0)
    repo = _env_repo(lux_rows=[_row(1000 - i, light=lux) for i in range(n)])
    alerts = detect_conflicts(_as_repo(repo), _plant_db({"ideal_light_lux_min": 1000}), 1, {}, {})
    assert [a.alert_type for a in alerts] == (["low_light"] if fires else [])
    assert repo.calls == [(1, 6), (2, 6), (1, 168)]
    if fires:
        assert alerts[0].data == {"avg_lux": float(lux), "min_lux": 1000, "seasonal_min_lux": 1000.0}


@pytest.mark.parametrize(("n", "humidity", "fires"), [(5, 44.5, True), (4, 44.5, False), (5, 45.0, False)])
def test_detect_conflicts_low_humidity_needs_five_samples_strictly_15_below(
    n: int, humidity: float, *, fires: bool
) -> None:
    """A low-humidity conflict needs five samples strictly 15 points below the minimum."""
    repo = _env_repo(hum_rows=[_row(1000 - i, humidity=humidity) for i in range(n)])
    alerts = detect_conflicts(_as_repo(repo), _plant_db({"ideal_humidity_min": 60}), 1, {}, {})
    assert [a.alert_type for a in alerts] == (["low_env_humidity"] if fires else [])
    assert repo.calls == [(1, 6), (2, 6), (1, 48)]
