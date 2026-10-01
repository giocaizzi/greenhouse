"""Hypothesis properties of the engine's pure helpers (characterization).

Pins what holds *today* for ``logic/cleaning.py``, ``logic/timing.py``,
``logic/stress.py``, ``logic/trends.py``, ``logic/fallback.py`` and
``logic/plant_needs.py``. Where an intuitive property does not hold, the actual
behavior is pinned with an explicit example (``*_current_behavior``).

Hypothesis runs derandomized with a modest example budget so the suite is
deterministic and fast; no assert depends on shrinking.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import time_machine
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from engine_grid import plant_database
from golden import FROZEN_INSTANT
from greenhouse_core.constants import (
    DEFAULT_DURATION_MINUTES,
    DEFAULT_SEASON_MULTIPLIER_INDOOR,
    DEFAULT_SEASON_MULTIPLIER_OUTDOOR,
    MAX_INTERVAL_HOURS,
    MIN_INTERVAL_HOURS,
    SENSOR_PHYSICAL_RANGES,
    TREND_MOISTURE_THRESHOLD,
)
from greenhouse_core.logic.cleaning import clean_readings, clean_readings_around, clean_readings_desc
from greenhouse_core.logic.decision import Action, SensorSnapshot, Trends, TriggerCode
from greenhouse_core.logic.fallback import temperature_based_decision
from greenhouse_core.logic.plant_needs import (
    analyze_water_needs,
    get_ideal_humidity_range,
    get_ideal_temp_range,
    parse_moisture_target,
)
from greenhouse_core.logic.stress import detect_stress_conditions
from greenhouse_core.logic.timing import (
    is_within_irrigation_window,
    is_within_quiet_hours,
    season_for,
    seasonal_multiplier,
)
from greenhouse_core.logic.trends import analyze_historical_trends

PROFILE = settings(
    derandomize=True,
    deadline=None,
    max_examples=150,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
NUMERIC = ("temperature", "soil_moisture", "env_humidity", "light")
TZS = ["UTC", "Europe/Rome", "America/New_York", "Australia/Sydney", "Asia/Kolkata", "Not/AZone", None]
SEASONS = ("winter", "spring", "summer", "autumn")


@dataclass
class Row:
    """Duck-typed ``SensorReading`` (only the attributes the cleaner reads)."""

    timestamp: int
    temperature: float | None = None
    soil_moisture: float | None = None
    env_humidity: float | None = None
    light: int | None = None
    battery_state: str | None = None
    water_warning: bool | None = None


def _maybe(strategy):
    return st.one_of(st.none(), strategy)


_value = {
    "temperature": _maybe(st.floats(-60, 100, allow_nan=False)),
    "soil_moisture": _maybe(st.floats(-10, 120, allow_nan=False)),
    "env_humidity": _maybe(st.floats(-10, 120, allow_nan=False)),
    "light": _maybe(st.integers(-100, 250_000)),
}


@st.composite
def series(draw, min_size=0, max_size=30):
    """One sensor's readings: unique timestamps, any insertion order."""
    stamps = draw(st.lists(st.integers(0, 10**6), min_size=min_size, max_size=max_size, unique=True))
    rows = [
        Row(
            timestamp=ts,
            battery_state=draw(_maybe(st.sampled_from(["low", "middle", "high"]))),
            water_warning=draw(_maybe(st.booleans())),
            **{f: draw(_value[f]) for f in NUMERIC},
        )
        for ts in stamps
    ]
    return draw(st.permutations(rows))


# ── cleaning ─────────────────────────────────────────────────────────────────


@PROFILE
@given(series())
def test_clean_readings_is_a_masked_subset_sorted_ascending(rows):
    """Every input row comes back once (ASC); each numeric value is kept verbatim or nulled."""
    snapshot = copy.deepcopy(rows)

    cleaned = clean_readings(rows)

    assert rows == snapshot  # input never mutated
    assert [c.timestamp for c in cleaned] == sorted(r.timestamp for r in rows)
    by_ts = {r.timestamp: r for r in rows}
    for c in cleaned:
        raw = by_ts[c.timestamp]
        assert (c.battery_state, c.water_warning) == (raw.battery_state, raw.water_warning)
        for f in NUMERIC:
            value = getattr(c, f)
            assert value is None or value == getattr(raw, f)
            if value is not None:
                low, high = SENSOR_PHYSICAL_RANGES[f]
                assert low <= value <= high


@PROFILE
@given(series())
def test_clean_readings_desc_is_exact_reverse(rows):
    assert clean_readings_desc(rows) == list(reversed(clean_readings(rows)))


@PROFILE
@given(series(max_size=4))
def test_clean_readings_short_series_only_range_gates(rows):
    """Below CLEANING_HAMPEL_MIN_READINGS (5) per metric, only the range gate removes values."""
    for c in clean_readings(rows):
        raw = next(r for r in rows if r.timestamp == c.timestamp)
        for f in NUMERIC:
            low, high = SENSOR_PHYSICAL_RANGES[f]
            raw_value = getattr(raw, f)
            expected = raw_value if raw_value is not None and low <= raw_value <= high else None
            assert getattr(c, f) == expected


@PROFILE
@given(st.floats(0, 100, allow_nan=False), st.integers(0, 40))
def test_clean_readings_flat_run_is_untouched(level, n):
    rows = [Row(timestamp=i * 600, soil_moisture=level) for i in range(n)]

    assert [c.soil_moisture for c in clean_readings(rows)] == [level] * n


@PROFILE
@given(series(), st.integers(0, 10**6))
def test_clean_readings_around_preserves_partition_and_matches_joint_cleaning(rows, split_ts):
    before = [r for r in rows if r.timestamp < split_ts]
    after = [r for r in rows if r.timestamp >= split_ts]

    cleaned_before, cleaned_after = clean_readings_around(before, after)

    assert len(cleaned_before) == len(before) and len(cleaned_after) == len(after)
    assert cleaned_before + cleaned_after == clean_readings(rows)


def test_clean_readings_is_not_idempotent_current_behavior():
    """Pins current behavior: cleaning a cleaned series can drop *more* values.

    Removing a spike shrinks/shifts the neighbours' Hampel windows, so a value that
    was in-band on the first pass can be flagged on the second. Consumers clean
    exactly once (raw rows → cleaned view), so this is an observation, not a bug.
    """
    soils = [0.0, 0.0, 0.0, 10.0, 0.0, 10.0]
    rows = [Row(timestamp=i * 600, soil_moisture=s) for i, s in enumerate(soils)]

    once = clean_readings(rows)
    twice = clean_readings(once)

    assert [c.soil_moisture for c in once] == [0.0, 0.0, 0.0, None, 0.0, 10.0]
    assert [c.soil_moisture for c in twice] == [0.0, 0.0, 0.0, None, 0.0, None]


# ── timing ───────────────────────────────────────────────────────────────────

_YEAR_START = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
_YEAR_END = int(datetime(2027, 1, 1, tzinfo=UTC).timestamp())
_NORTH = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring"}
_NORTH |= {6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}


def _local_month(ts, tz):
    try:
        zone = ZoneInfo(tz) if tz else ZoneInfo("UTC")
    except Exception:  # bad names fall back to UTC in production too
        zone = ZoneInfo("UTC")
    return datetime.fromtimestamp(ts, tz=zone).month


@PROFILE
@given(st.integers(_YEAR_START, _YEAR_END - 1), st.sampled_from(TZS))
def test_season_for_is_the_meteorological_season_of_the_local_month(ts, tz):
    month = _local_month(ts, tz)

    assert season_for(ts, tz_name=tz) == _NORTH[month]
    assert season_for(ts, tz_name=tz, hemisphere="southern") == _NORTH[(month + 5) % 12 + 1]


@pytest.mark.parametrize(
    ("tz", "expected"),
    [
        ("UTC", {"winter": 2160, "spring": 2208, "summer": 2208, "autumn": 2184}),
        # DST shifts one hour between spring and autumn in local-month terms.
        ("Europe/Rome", {"winter": 2160, "spring": 2207, "summer": 2208, "autumn": 2185}),
        ("Australia/Sydney", {"winter": 2160, "spring": 2209, "summer": 2208, "autumn": 2183}),
    ],
)
def test_season_for_hourly_census_over_2026(tz, expected):
    """Exhaustive: every hour of 2026 maps to a season; per-season hour counts pinned."""
    counts = dict.fromkeys(SEASONS, 0)
    for ts in range(_YEAR_START, _YEAR_END, 3600):
        counts[season_for(ts, tz_name=tz)] += 1

    assert counts == expected


@pytest.mark.parametrize(
    ("local", "season", "utc_season"),
    [
        ((2026, 2, 28, 23, 59, 59), "winter", "winter"),
        ((2026, 3, 1, 0, 0, 0), "spring", "winter"),  # 23:00 UTC Feb 28
        ((2026, 5, 31, 23, 59, 59), "spring", "spring"),
        ((2026, 6, 1, 0, 0, 0), "summer", "spring"),  # 22:00 UTC May 31 (CEST)
        ((2026, 8, 31, 23, 59, 59), "summer", "summer"),
        ((2026, 9, 1, 0, 0, 0), "autumn", "summer"),
        ((2026, 11, 30, 23, 59, 59), "autumn", "autumn"),
        ((2026, 12, 1, 0, 0, 0), "winter", "autumn"),
    ],
)
def test_season_for_boundaries_are_local_midnight(local, season, utc_season):
    ts = int(datetime(*local, tzinfo=ZoneInfo("Europe/Rome")).timestamp())

    assert season_for(ts, tz_name="Europe/Rome") == season
    assert season_for(ts, tz_name="UTC") == utc_season


_multiplier_table = st.dictionaries(st.sampled_from(SEASONS), _maybe(st.floats(0.05, 3.0, allow_nan=False)))


@PROFILE
@given(
    st.sampled_from(SEASONS),
    st.sampled_from(["indoor", "outdoor", "greenhouse"]),
    _maybe(_multiplier_table),
    _maybe(_multiplier_table),
)
def test_seasonal_multiplier_precedence_plant_then_category_then_builtin(season, env, plant, category):
    result = seasonal_multiplier(season, environment=env, plant_override=plant, category_override=category)

    assert isinstance(result, float)
    if plant and plant.get(season) is not None:
        assert result == plant[season]
    elif category and category.get(season) is not None:
        assert result == category[season]
    else:
        table = DEFAULT_SEASON_MULTIPLIER_OUTDOOR if env == "outdoor" else DEFAULT_SEASON_MULTIPLIER_INDOOR
        assert result == table[season]


def test_seasonal_multiplier_range_over_bundled_plant_data():
    """Every multiplier reachable from the bundled plant DB lies in [0.1, 1.6]."""
    pdb = plant_database()
    seen = set()
    species = pdb.list_species() + ["Plantus fictitius"]
    for name in species:
        for category in pdb.list_categories() + [None]:
            care = pdb.get_care_data(species=name, category=category)
            for env, key in (
                ("indoor", "season_frequency_multiplier"),
                ("outdoor", "season_frequency_multiplier_outdoor"),
            ):
                for season in SEASONS:
                    seen.add(
                        seasonal_multiplier(
                            season,
                            environment=env,
                            plant_override=care.get(key),
                            category_override=care["_category_defaults"].get(key),
                        )
                    )

    assert min(seen) == 0.1 and max(seen) == 1.6
    assert all(0.1 <= m <= 1.6 for m in seen)


@PROFILE
@given(st.integers(0, 23), st.integers(0, 23), st.integers(_YEAR_START, _YEAR_END), st.sampled_from(TZS))
def test_quiet_hours_window_and_its_complement_partition_the_day(start, end, ts, tz):
    inside = is_within_quiet_hours(start_hour=start, end_hour=end, now_unix=ts, tz_name=tz)
    swapped = is_within_quiet_hours(start_hour=end, end_hour=start, now_unix=ts, tz_name=tz)

    if start == end:
        assert inside is False and swapped is False
    else:
        assert inside != swapped
    assert is_within_quiet_hours(start_hour=None, end_hour=end, now_unix=ts, tz_name=tz) is False
    assert is_within_quiet_hours(start_hour=start, end_hour=None, now_unix=ts, tz_name=tz) is False


@PROFILE
@given(
    st.lists(
        st.builds(
            SimpleNamespace,
            start_hour=st.integers(0, 23),
            end_hour=st.integers(0, 23),
            weekday_mask=st.integers(0, 127),
        ),
        max_size=4,
    ),
    st.integers(_YEAR_START, _YEAR_END),
    st.sampled_from(TZS),
)
def test_irrigation_window_is_any_matching_row_and_no_rows_means_allowed(windows, ts, tz):
    result = is_within_irrigation_window(windows, now_unix=ts, tz_name=tz)

    if not windows:
        assert result is True
    else:
        each = [is_within_irrigation_window([w], now_unix=ts, tz_name=tz) for w in windows]
        assert result == any(each)
        assert not any(each[i] for i, w in enumerate(windows) if w.weekday_mask == 0 or w.start_hour == w.end_hour)


# ── stress / trends (fake repository) ────────────────────────────────────────


class FakeRepo:
    """Minimal read-only repository double for the trend / stress helpers."""

    def __init__(self, plants=(), readings=None, events=(), irrigator=True):
        self._plants = list(plants)
        self._readings = readings or {}
        self._events = list(events)
        self._irrigator = SimpleNamespace(id=1) if irrigator else None

    def get_plants_in_cluster(self, cluster_id):
        return self._plants

    def get_sensors_in_cluster(self, cluster_id):
        return [SimpleNamespace(id=sid) for sid in sorted(self._readings)]

    def get_recent_readings(self, sensor_id, hours=24):
        return sorted(self._readings[sensor_id], key=lambda r: r.timestamp, reverse=True)

    def get_irrigator_for_cluster(self, cluster_id):
        return self._irrigator

    def get_recent_events(self, irrigator_id, hours=24):
        return self._events


_plants = st.lists(
    st.builds(
        SimpleNamespace,
        species=st.sampled_from(["Monstera deliciosa", "Dracaena marginata", "Unknown fern", "Plantus fictitius"]),
        category=st.sampled_from(["tropical", "fern", "cacti", None]),
    ),
    max_size=3,
)
_snapshots = st.builds(
    SensorSnapshot,
    avg_temperature=_maybe(st.floats(-40, 80, allow_nan=False)),
    avg_env_humidity=_maybe(st.floats(0, 100, allow_nan=False)),
    avg_soil_moisture=_maybe(st.floats(0, 100, allow_nan=False)),
    avg_light=_maybe(st.floats(16, 200_000, allow_nan=False)),
    water_warnings=st.lists(st.sampled_from(["Soil A", "Soil B"]), max_size=2, unique=True),
)
_trends = st.builds(
    Trends,
    soil_moisture_trend=st.sampled_from([None, "declining", "rising", "stable"]),
    soil_moisture_delta=st.floats(-100, 100, allow_nan=False),
    temperature_trend=st.sampled_from([None, "rising", "falling", "stable"]),
    irrigation_frequency_low=st.booleans(),
    irrigation_frequency_high=st.booleans(),
)


@PROFILE
@given(_plants, _snapshots, _trends)
def test_detect_stress_conditions_rules(plants, snapshot, trends):
    """No exceptions over the valid domain; each indicator is set exactly by its documented rule."""
    with time_machine.travel(FROZEN_INSTANT, tick=False):
        stress = detect_stress_conditions(FakeRepo(plants), plant_database(), 1, snapshot, trends)

    assert (stress.water_warning is not None) == bool(snapshot.water_warnings)
    soil = snapshot.avg_soil_moisture
    expect_water_stress = soil is not None and (
        soil < 30 or (soil < 40 and trends.soil_moisture_trend == "declining" and trends.soil_moisture_delta < -10)
    )
    assert (stress.water_stress is not None) == expect_water_stress
    expect_over = (
        soil is not None and soil > 70 and (trends.irrigation_frequency_high or trends.soil_moisture_trend == "rising")
    )
    assert (stress.over_watering is not None) == expect_over
    if not plants:
        assert stress.heat_stress is None and stress.low_env_humidity is None and stress.low_light is None
    assert stress.learning_alerts == []


_trend_series = st.lists(
    st.tuples(st.integers(0, 48 * 3600), _maybe(st.floats(0, 100, allow_nan=False)), _maybe(st.floats(-5, 45))),
    max_size=24,
    unique_by=lambda t: t[0],
)


@PROFILE
@given(
    st.lists(_trend_series, max_size=3),
    st.lists(
        st.builds(
            SimpleNamespace,
            action=st.sampled_from(["start", "schedule_updated", "stop"]),
            duration_minutes=_maybe(st.integers(0, 10)),
        ),
        max_size=30,
    ),
    st.booleans(),
)
def test_analyze_historical_trends_labels_are_consistent(sensor_series, events, has_irrigator):
    readings = {
        sid: [Row(timestamp=ts, soil_moisture=soil, temperature=temp) for ts, soil, temp in s]
        for sid, s in enumerate(sensor_series, start=1)
    }

    trends = analyze_historical_trends(FakeRepo(readings=readings, events=events, irrigator=has_irrigator), 1)

    assert trends.soil_moisture_trend in (None, "declining", "rising", "stable")
    assert trends.temperature_trend in (None, "rising", "falling", "stable")
    delta = trends.soil_moisture_delta
    if trends.soil_moisture_trend is None:
        assert delta == 0.0
    else:
        expected = (
            "declining"
            if delta < -TREND_MOISTURE_THRESHOLD
            else "rising"
            if delta > TREND_MOISTURE_THRESHOLD
            else "stable"
        )
        assert trends.soil_moisture_trend == expected
    assert not (trends.irrigation_frequency_low and trends.irrigation_frequency_high)
    starts = [e for e in events if e.action == "start" and e.duration_minutes]
    if not has_irrigator or not starts:
        assert not trends.irrigation_frequency_low and not trends.irrigation_frequency_high
    else:
        assert trends.irrigation_frequency_high == (len(starts) / 7 > 3)


# ── fallback / plant_needs ───────────────────────────────────────────────────


@PROFILE
@given(st.floats(-40, 60, allow_nan=False), st.sampled_from(["low", "medium", "high", "unknown"]))
def test_temperature_fallback_always_irrigates_within_interval_bounds(temp, needs):
    decision = temperature_based_decision(None, 1, 0, temp=temp, water_needs=needs, temp_range=None, config=None)

    assert decision.action is Action.IRRIGATE
    assert decision.duration_minutes == DEFAULT_DURATION_MINUTES
    assert MIN_INTERVAL_HOURS <= decision.interval_hours <= MAX_INTERVAL_HOURS
    assert [r.code for r in decision.reasons] == [TriggerCode.TEMP_FALLBACK]
    assert decision.confidence == 0.6


@PROFILE
@given(st.floats(-40, 60, allow_nan=False))
def test_temperature_fallback_interval_is_monotone_in_temperature(temp):
    """Hotter never means a longer interval (for each water-needs level)."""
    for needs in ("low", "medium", "high"):
        cooler = temperature_based_decision(None, 1, 0, temp=temp, water_needs=needs, temp_range=None, config=None)
        hotter = temperature_based_decision(
            None, 1, 0, temp=temp + 1.0, water_needs=needs, temp_range=None, config=None
        )
        assert hotter.interval_hours <= cooler.interval_hours


def test_temperature_fallback_without_temp_or_config_is_no_data():
    decision = temperature_based_decision(None, 1, 0, temp=None, water_needs="medium", temp_range=None, config=None)

    assert (decision.action, decision.confidence, [r.code for r in decision.reasons]) == (
        Action.SKIP,
        0.2,
        [TriggerCode.NO_DATA],
    )


@PROFILE
@given(st.text(max_size=12))
def test_parse_moisture_target_never_raises(text):
    low, high = parse_moisture_target(text)

    assert isinstance(low, float) and isinstance(high, float)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("45-65", (45.0, 65.0)), ("30-50", (30.0, 50.0)), ("", (45.0, 65.0)), ("abc", (45.0, 65.0)), ("70", (45.0, 65.0))],
)
def test_parse_moisture_target_examples(text, expected):
    assert parse_moisture_target(text) == expected


def test_parse_moisture_target_inverted_and_negative_current_behavior():
    """Pins current behavior: no validation — '65-45' stays inverted, '-5-10' falls back."""
    assert parse_moisture_target("65-45") == (65.0, 45.0)
    assert parse_moisture_target("-5-10") == (45.0, 65.0)


_care = st.lists(
    st.fixed_dictionaries(
        {},
        optional={
            "water_needs": st.sampled_from(["low", "medium", "high", "extreme"]),
            "ideal_temp_min_c": st.integers(0, 30),
            "ideal_temp_max_c": st.integers(0, 45),
            "ideal_humidity_min": st.integers(0, 100),
            "ideal_humidity_max": st.integers(0, 100),
        },
    ),
    max_size=4,
)


@PROFILE
@given(_care)
def test_plant_needs_aggregates(care):
    assert analyze_water_needs(care) in ("low", "medium", "high")
    temp_range = get_ideal_temp_range(care)
    mins = [d["ideal_temp_min_c"] for d in care if d.get("ideal_temp_min_c")]
    maxs = [d["ideal_temp_max_c"] for d in care if d.get("ideal_temp_max_c")]
    assert temp_range == ((min(mins), max(maxs)) if mins and maxs else None)
    hum = get_ideal_humidity_range(care)
    assert hum is None or hum[0] == min(d["ideal_humidity_min"] for d in care if d.get("ideal_humidity_min"))


def test_plant_needs_zero_is_treated_as_missing_current_behavior():
    """Pins current behavior: truthiness filter drops a 0 °C / 0 % bound."""
    assert get_ideal_temp_range([{"ideal_temp_min_c": 0, "ideal_temp_max_c": 20}]) is None
    assert get_ideal_humidity_range([{"ideal_humidity_min": 0, "ideal_humidity_max": 50}]) is None


def test_analyze_water_needs_unknown_level_counts_as_medium():
    assert analyze_water_needs([{"water_needs": "extreme"}, {"water_needs": "high"}]) == "medium"
    assert analyze_water_needs([]) == "medium"
