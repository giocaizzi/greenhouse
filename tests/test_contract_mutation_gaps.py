"""Gate 1 mutation-campaign gap tests (core) — characterization, current behavior.

Each test kills one mutant that survived the Phase-1 safety net (see
``refactor/gate1/mutation.md``, ids in the test docstrings). They pin what the
code does TODAY at boundaries and orderings the golden grid did not reach;
nothing here asserts what the code *should* do. Engine cases reuse the
pytest-free ``engine_grid`` harness: every case is evaluated under its own
frozen instant on a fresh in-memory schema, no network, no wall clock.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import greenhouse_core.learning.issues as issues_mod
import greenhouse_core.learning.report as report_mod
from engine_grid import BASE_AT, DAY, HOUR, Case, Event, Reading, SensorSpec, plant_database, run_case
from golden import FROZEN_TS
from greenhouse_core.learning import IrrigationLearner
from greenhouse_core.learning.models import Alert, PlantProfile
from greenhouse_core.learning.profiling import compute_sensor_response, get_plant_profile
from greenhouse_core.logic.cleaning import clean_readings
from greenhouse_core.logic.timing import is_within_quiet_hours, local_now
from greenhouse_core.utils import effective_light_threshold


@pytest.fixture(autouse=True)
def _hermetic(clean_env):
    """Isolate env vars / TZ like the other characterization suites."""


def _sensor(*readings: Reading, name: str = "Gap Soil") -> SensorSpec:
    return SensorSpec(name=name, readings=readings, plant=0)


def _decide(case_id: str, **kw) -> dict:
    """Run one Monstera (target 45-60 %, temp 18-29 °C) case and return the decision dump."""
    record = run_case(Case(id=f"gaps/{case_id}", family="gaps", at=BASE_AT, **kw))
    assert len(record["decision_logs"]) == 1
    return record["decision"]


def _codes(decision: dict) -> list[str]:
    return [r["code"] for r in decision["reasons"]]


# ── logic/engine.py ─────────────────────────────────────────────────────────


def test_water_warning_outranks_critical_stress_when_both_fire():
    """engine-13: the sensor's own water_warning rule runs BEFORE the critical-stress rule.

    A 20 % reading (critical low → water_stress) that also carries the device
    water_warning flag ends on WATER_WARNING alone (confidence 0.92), not on
    WATER_STRESS (0.95).
    """
    decision = _decide("ww-and-stress", sensors=(_sensor(Reading(age_s=600, soil=20.0, water_warning=True)),))
    assert _codes(decision) == ["water_warning"]
    assert decision["confidence"] == 0.92
    assert decision["stress_indicators"]["water_stress"] is not None
    assert (decision["action"], decision["duration_minutes"], decision["interval_hours"]) == ("irrigate", 3, 6)


@pytest.mark.parametrize(
    ("soil", "code"),
    [(60.0, "sensor_adequate"), (60.5, "sensor_wet")],
)
def test_soil_exactly_at_target_max_is_adequate(soil, code):
    """engine-49: ``avg_soil <= target_max`` is inclusive — 60 % on a 45-60 plant is adequate."""
    decision = _decide(f"soil-max-{soil}", sensors=(_sensor(Reading(age_s=600, soil=soil)),))
    assert _codes(decision)[0] == code
    assert decision["action"] == "skip"


@pytest.mark.parametrize(
    ("temp", "expected"),
    [(32.0, False), (32.5, True)],
)
def test_temp_high_adjustment_is_strictly_above_ideal_plus_offset(temp, expected):
    """engine-51: TEMP_HIGH fires only for ``avg_temp > ideal_max + 3`` (29 + 3 = 32 °C exclusive)."""
    decision = _decide(f"temp-high-{temp}", sensors=(_sensor(Reading(age_s=600, soil=50.0, temp=temp)),))
    assert ("temp_high" in _codes(decision)) is expected


@pytest.mark.parametrize(
    ("temps", "expected"),
    [((23.0, 23.0, 27.0, 27.0), False), ((23.5, 23.5, 27.5, 27.5), True)],
)
def test_rising_temperature_trend_needs_strictly_above_25c(temps, expected):
    """engine-57: TREND_TEMP_RISING needs a rising trend AND ``avg_temp > 25`` (exclusive).

    Four hourly readings (soil 50 %): the first pair averages exactly 25 °C,
    the second 25.5 °C; both are "rising" (+4 °C between halves).
    """
    readings = tuple(Reading(age_s=(3 - i) * HOUR, soil=50.0, temp=t) for i, t in enumerate(temps))
    decision = _decide(f"trend-hot-{temps[0]}", sensors=(_sensor(*readings),))
    assert decision["trends"]["temperature_trend"] == "rising"
    assert ("trend_temp_rising" in _codes(decision)) is expected


def test_vacation_budget_equal_to_duration_is_not_trimmed():
    """engine-28: headroom that exactly covers the run (``binding == duration``) leaves it untouched.

    100 L tank, 1 L/min, a 10-day vacation that began 12 h ago → 9.5 L allowed
    today; a 7-minute run 8 h ago leaves 2.5 L → floor = 2 min, exactly the
    SENSOR_DRY dose. No VACATION_RATIONING reason is appended.
    """
    decision = _decide(
        "vacation-budget-equal",
        sensors=(_sensor(Reading(age_s=600, soil=38.0)),),
        vacation=(-DAY // 2, 9 * DAY + DAY // 2),
        reservoir_l=100.0,
        flow_rate=1.0,
        events=(Event(8 * HOUR, duration=7),),
    )
    assert _codes(decision) == ["sensor_dry", "vacation_active"]
    assert (decision["action"], decision["duration_minutes"]) == ("irrigate", 2)


# ── logic/decision.py ───────────────────────────────────────────────────────


def test_light_only_sensor_data_counts_as_data():
    """decision-04: ``SensorSnapshot.has_data`` includes ``avg_light``.

    A sensor that reports only lux keeps the sensor path (base SKIP + light
    adjustment) instead of falling back to the no-data temperature rule.
    """
    decision = _decide("light-only", sensors=(_sensor(Reading(age_s=600, light=60)),))
    assert decision["sensor_snapshot"]["avg_light"] == 60
    assert "no_data" not in _codes(decision)
    assert decision["confidence"] == 0.5


# ── logic/sensors.py ────────────────────────────────────────────────────────


def test_water_warning_names_are_deduplicated_per_sensor():
    """sensors-03: two flagged readings from one sensor name it once in the snapshot and the reason."""
    readings = (Reading(age_s=1200, soil=50.0, water_warning=True), Reading(age_s=600, soil=50.0, water_warning=True))
    decision = _decide("ww-dedup", sensors=(_sensor(*readings),))
    assert decision["sensor_snapshot"]["water_warnings"] == ["Gap Soil"]
    assert decision["reasons"][0]["message"] == "sensor alert: device alert on: Gap Soil"


# ── logic/stress.py ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("fraction", "expected"), [(0.35, True), (0.45, False)])
def test_low_light_stress_fires_below_40_percent_of_the_seasonal_minimum(fraction, expected):
    """stress-02: ``low_light`` needs ``avg_light < 0.4 × effective_light_threshold(lux_min)``.

    Monstera's summer baseline is 150 lux; April's seasonal factor applies (UTC month).
    """
    lux = round(effective_light_threshold(150, month=BASE_AT.month) * fraction)
    decision = _decide(f"low-light-{fraction}", sensors=(_sensor(Reading(age_s=600, soil=50.0, light=lux)),))
    assert (decision["stress_indicators"]["low_light"] is not None) is expected


def _hourly(field: str, values, **const) -> SensorSpec:
    """Hourly readings ending 0 s ago (oldest → newest); < 5 points, so no spike filtering."""
    n = len(values)
    return _sensor(*(Reading(age_s=(n - 1 - i) * HOUR, **{field: v}, **const) for i, v in enumerate(values)))


@pytest.mark.parametrize(("soils", "saturated"), [((65.0, 65.0, 75.0, 75.0), False), ((65.5, 65.5, 75.5, 75.5), True)])
def test_over_watering_needs_average_strictly_above_saturation(soils, saturated):
    """stress-10: ``avg_soil > 70`` (exclusive) + rising trend → over_watering."""
    decision = _decide(f"saturated-{soils[0]}", sensors=(_hourly("soil", soils),))
    assert decision["trends"]["soil_moisture_trend"] == "rising"
    assert (decision["stress_indicators"]["over_watering"] is not None) is saturated


# ── logic/trends.py ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("soils", "label"), [((55.0, 55.0, 50.0, 50.0), "stable"), ((55.0, 55.0, 49.5, 49.5), "declining")]
)
def test_soil_trend_declining_needs_delta_strictly_below_minus_threshold(soils, label):
    """trends-03: a -5 % half-over-half delta is still "stable"; -5.5 % is "declining"."""
    decision = _decide(f"soil-trend-{soils[-1]}", sensors=(_hourly("soil", soils),))
    assert decision["trends"]["soil_moisture_trend"] == label


@pytest.mark.parametrize(
    ("temps", "label"), [((22.0, 22.0, 20.0, 20.0), "stable"), ((22.0, 22.0, 19.5, 19.5), "falling")]
)
def test_temperature_trend_falling_needs_delta_strictly_below_minus_threshold(temps, label):
    """trends-10: a -2 °C delta is still "stable"; -2.5 °C is "falling"."""
    decision = _decide(f"temp-trend-{temps[-1]}", sensors=(_hourly("temp", temps, soil=50.0),))
    assert decision["trends"]["temperature_trend"] == label


def test_zero_celsius_readings_are_ignored_by_the_temperature_trend_current_behavior():
    """trends-04: Pins current (buggy) behavior: ``if r.temperature`` drops 0.0 °C readings.

    With the first half all at 0.0 °C there is no "first half" mean, so no
    temperature trend at all (``None``) although the second half is 10 °C
    warmer — see REFACTOR_NOTES.md (decision engine, plant_needs/trends zero
    truthiness).
    """
    decision = _decide("temp-trend-zero", sensors=(_hourly("temp", (0.0, 0.0, 10.0, 10.0), soil=50.0),))
    assert decision["trends"]["temperature_trend"] is None


@pytest.mark.parametrize(("starts", "high"), [(21, False), (22, True)])
def test_irrigation_frequency_high_needs_more_than_three_starts_per_day(starts, high):
    """trends-07: ``starts / 7 > 3`` (exclusive): 21 starts in a week is not "high", 22 is."""
    events = tuple(Event(7 * HOUR + i * 6 * HOUR, duration=2) for i in range(starts))
    decision = _decide(f"freq-{starts}", sensors=(_sensor(Reading(age_s=600, soil=50.0)),), events=events)
    assert decision["trends"]["irrigation_frequency_high"] is high


# ── logic/cleaning.py ───────────────────────────────────────────────────────


def _rows(soils):
    return [
        SimpleNamespace(
            timestamp=1_000 + 600 * i,
            temperature=None,
            soil_moisture=s,
            env_humidity=None,
            light=None,
            battery_state=None,
            water_warning=None,
        )
        for i, s in enumerate(soils)
    ]


def _clean_soil(soils):
    return [r.soil_moisture for r in clean_readings(_rows(soils))]


@pytest.mark.parametrize(
    ("spike", "kept"),
    [(53.0, True), (53.5, False)],
)
def test_hampel_spike_test_is_strictly_greater_than_threshold(spike, kept):
    """cleaning-04: a flat run has MAD 0 → scale = floor 1.0 → threshold 3.0, exclusive.

    A deviation of exactly 3.0 % survives; 3.5 % is dropped.
    """
    cleaned = _clean_soil([50.0, 50.0, 50.0, spike, 50.0, 50.0, 50.0])
    assert cleaned[3] == (spike if kept else None)
    assert cleaned[:3] + cleaned[4:] == [50.0] * 6


def test_hampel_window_is_centered_with_full_left_context_current_behavior():
    """cleaning-09: the window spans ``i-3 .. i+3`` (clamped).

    Pins current behavior on a step change: in ``40,40,40,60,60`` the FIRST 60
    is judged against the window ``40,40,40,60,60`` (median 40, MAD 0) and
    dropped, while the last 60 (window ``40,40,60,60``) is kept.
    """
    assert _clean_soil([40.0, 40.0, 40.0, 60.0, 60.0]) == [40.0, 40.0, 40.0, None, 60.0]


# ── logic/timing.py ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("tz_name", ["Not/AZone", "", None])
def test_unknown_timezone_falls_back_to_utc(tz_name):
    """timing-09: a missing or unknown tz name resolves to UTC (not any other zone)."""
    ts = int(BASE_AT.timestamp())  # 10:00 UTC
    assert local_now(ts, tz_name).utcoffset().total_seconds() == 0
    assert is_within_quiet_hours(start_hour=10, end_hour=11, now_unix=ts, tz_name=tz_name) is True


# ── repository.py ───────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_db, frozen_clock):
    return tmp_db


def test_acknowledge_leaves_a_resolved_alert_resolved(repo):
    """repository-11: only an ``open`` alert moves to ``acknowledged``; a resolved one is untouched."""
    alert = repo.upsert_alert("gap:alert", "irrigation", "gap_code", "Gap", "msg", severity="warning")
    repo.resolve_alert(alert.id)
    row = repo.acknowledge_alert(alert.id)
    assert (row.status, row.acknowledged_at, row.resolved_at) == ("resolved", None, FROZEN_TS)


def _probe(repo) -> int:
    cid = repo.add_cluster("Repo Gap")
    return repo.add_sensor(
        cluster_id=cid,
        tuya_device_id="fake_tuya_sensor_repo0001",
        name="Repo Probe",
        sensor_type="soil_moisture",
        config={},
    )


def test_get_latest_reading_is_the_newest_row(repo):
    """repository-17: ``get_latest_reading`` orders by timestamp DESC (the health/freshness read model)."""
    sid = _probe(repo)
    for age, soil in ((7200, 10.0), (600, 30.0), (3600, 20.0)):
        repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - age, soil_moisture=soil)
    latest = repo.get_latest_reading(sid)
    assert (latest.timestamp, latest.soil_moisture) == (FROZEN_TS - 600, 30.0)


def test_upsert_alert_refreshes_severity(repo):
    """repository-21: re-raising an alert overwrites its severity (and message/title) with the new values."""
    repo.upsert_alert("gap:sev", "health", "gap_code", "Old", "old msg", severity="warning")
    row = repo.upsert_alert("gap:sev", "health", "gap_code", "New", "new msg", severity="critical")
    assert (row.severity, row.title, row.message, row.occurrence_count) == ("critical", "New", "new msg", 2)


def test_readings_around_puts_the_event_instant_in_both_windows(repo):
    """repository-18: both windows are inclusive at the event timestamp (the row appears twice)."""
    sid = _probe(repo)
    for offset in (-1800, -1, 0, 1, 1800):
        repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS + offset, soil_moisture=40.0)
    before, after = repo.get_readings_around(sid, FROZEN_TS, before_seconds=1800, after_seconds=1800)
    assert [r.timestamp - FROZEN_TS for r in before] == [-1800, -1, 0]
    assert [r.timestamp - FROZEN_TS for r in after] == [0, 1, 1800]


# ── learning/issues.py ──────────────────────────────────────────────────────


def _profile(sensor_id: int, *, efficiency: float, absorption: float, drainage: float = 0.0) -> PlantProfile:
    return PlantProfile(
        sensor_id=sensor_id,
        plant_id=None,
        sensor_name="Repo Probe",
        avg_absorption_per_minute=absorption,
        avg_drainage_per_hour=drainage,
        response_count=5,
        min_delta=0.0,
        max_delta=1.0,
        efficiency_score=efficiency,
    )


@pytest.mark.parametrize(
    ("efficiency", "absorption", "blocked"),
    [(0.2, 1.0, False), (0.5, 0.1, False), (0.2, 0.1, True)],
)
def test_blocked_drip_needs_low_efficiency_and_low_absorption(repo, monkeypatch, efficiency, absorption, blocked):
    """learning-02: ``blocked_drip`` needs BOTH efficiency < 0.3 AND absorption < 0.5 %/min."""
    sid = _probe(repo)
    cid = repo.get_sensor(sid).cluster_id
    monkeypatch.setattr(
        issues_mod,
        "get_plant_profile",
        lambda db, sensor: _profile(sensor.id, efficiency=efficiency, absorption=absorption),
    )
    alerts = issues_mod.detect_issues(repo, plant_database(), cid)
    assert [a.alert_type for a in alerts] == (["blocked_drip"] if blocked else [])


@pytest.mark.parametrize(("drainage", "rapid"), [(-5.0, False), (-5.5, True)])
def test_rapid_drainage_needs_drainage_strictly_below_threshold(repo, monkeypatch, drainage, rapid):
    """learning-12: ``rapid_drainage`` fires for ``avg_drainage_per_hour < -5`` (exclusive)."""
    sid = _probe(repo)
    cid = repo.get_sensor(sid).cluster_id
    monkeypatch.setattr(
        issues_mod,
        "get_plant_profile",
        lambda db, sensor: _profile(sensor.id, efficiency=1.0, absorption=1.0, drainage=drainage),
    )
    alerts = issues_mod.detect_issues(repo, plant_database(), cid)
    assert [a.alert_type for a in alerts] == (["rapid_drainage"] if rapid else [])


def _two_probe_cluster(repo, *, link_plant: bool = True):
    """Two probes (A linked to a Monstera, B unlinked) so ``detect_conflicts`` passes its 2-sensor gate."""
    cid = repo.add_cluster("Conflict Gap")
    pid = repo.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical") if link_plant else None
    a = repo.add_sensor(
        cluster_id=cid,
        tuya_device_id="fake_tuya_sensor_cfl0001",
        name="Probe A",
        sensor_type="soil_moisture",
        config={},
        plant_id=pid,
    )
    b = repo.add_sensor(
        cluster_id=cid,
        tuya_device_id="fake_tuya_sensor_cfl0002",
        name="Probe B",
        sensor_type="soil_moisture",
        config={},
    )
    return cid, a, b


@pytest.mark.parametrize(
    ("metric", "value", "samples", "flagged"),
    [
        ("light", 20, 4, None),
        ("light", 20, 5, "low_light"),
        ("env_humidity", 30.0, 4, None),
        ("env_humidity", 30.0, 5, "low_env_humidity"),
    ],
)
def test_conflict_light_and_humidity_checks_need_five_samples(repo, metric, value, samples, flagged):
    """learning-09 / learning-14: the low-light (7 d, daytime lux) and low-humidity (48 h) checks skip
    sensors with fewer than 5 samples.

    Monstera: summer minimum 150 lux (April factor 0.85, alert below half → < 63.75 lux) and
    ideal humidity ≥ 60 % (alert below 45 %).
    """
    cid, a, b = _two_probe_cluster(repo)
    for i in range(samples):
        repo.add_sensor_reading(sensor_id=a, timestamp=FROZEN_TS - 600 * (i + 1), soil_moisture=50.0, **{metric: value})
    repo.add_sensor_reading(sensor_id=b, timestamp=FROZEN_TS - 600, soil_moisture=50.0)
    alerts = issues_mod.detect_conflicts(repo, plant_database(), cid, {}, {})
    assert [a.alert_type for a in alerts] == ([flagged] if flagged else [])


@pytest.mark.parametrize(("delay", "counted"), [(600, True), (599, False)])
def test_post_irrigation_reading_counts_from_exactly_ten_minutes(repo, delay, counted):
    """learning-16 / learning-17: a reading ``>= 600 s`` after the start is a valid "post" sample
    (inclusive), and "pre" is the LAST reading before the start (30 %, not the older 20 %)."""
    sid = _probe(repo)
    start = FROZEN_TS - 3600
    repo.add_sensor_reading(sensor_id=sid, timestamp=start - 1500, soil_moisture=20.0)
    repo.add_sensor_reading(sensor_id=sid, timestamp=start - 60, soil_moisture=30.0)
    repo.add_sensor_reading(sensor_id=sid, timestamp=start + delay, soil_moisture=50.0)
    event = SimpleNamespace(id=1, timestamp=start, duration_minutes=4)
    response = compute_sensor_response(repo, repo.get_sensor(sid), event)
    if counted:
        assert (response.pre_moisture, response.post_moisture, response.delta_per_minute) == (30.0, 50.0, 5.0)
        assert response.reading_delay_seconds == 600
    else:
        assert response is None


def test_plant_profile_end_to_end(repo):
    """learning-18…25, learning-29: ``get_plant_profile`` on a hand-built 10-day history.

    Two ``start`` events (one with no duration → defaults to 2 min) and one ``stop``
    (ignored). Responses: +1.5 % (peak of 41.5/41.0, over 2 min) and +20 % (over 4
    min) → absorption mean(0.75, 5.0) = 2.875 %/min, efficiency = share of deltas
    > 2 % = 0.5. Drainage = mean of consecutive declines with 0.1 h < gap < 12 h:
    (-0.5 % / 0.25 h, -2 % / 0.5 h) = mean(-2, -4) = -3 %/h; the -20 % over a 15 h
    gap is excluded.
    """
    cid = repo.add_cluster("Profile Gap")
    irr = repo.add_irrigator(
        cluster_id=cid, tuya_device_id="fake_tuya_device_prof0001", name="Pump", irrigator_type="tuya_cloud", config={}
    )
    sid = repo.add_sensor(
        cluster_id=cid, tuya_device_id="fake_tuya_sensor_prof0001", name="Prof", sensor_type="soil_moisture", config={}
    )
    t1, t2, t3 = FROZEN_TS - 10 * DAY, FROZEN_TS - 5 * DAY, FROZEN_TS - 3 * DAY
    repo.add_irrigation_event(
        irrigator_id=irr, action="start", triggered_by="auto", duration_minutes=None, timestamp=t1
    )
    repo.add_irrigation_event(irrigator_id=irr, action="start", triggered_by="auto", duration_minutes=4, timestamp=t2)
    repo.add_irrigation_event(irrigator_id=irr, action="stop", triggered_by="manual", timestamp=t3)
    for ts, soil in [
        (t1 - 60, 40.0),
        (t1 + 900, 41.5),
        (t1 + 1800, 41.0),
        (t2 - 60, 30.0),
        (t2 + 1200, 50.0),
        (t3 - 60, 50.0),
        (t3 + 1200, 60.0),
        (t3 + 1200 + 15 * HOUR, 40.0),
        (t3 + 1200 + 15 * HOUR + 1800, 38.0),
    ]:
        repo.add_sensor_reading(sensor_id=sid, timestamp=ts, soil_moisture=soil)
    profile = get_plant_profile(repo, repo.get_sensor(sid))
    # learning-29: the learner facade uses the same 30-day default window.
    assert IrrigationLearner(repo, plant_database()).get_plant_profile(repo.get_sensor(sid)) == profile
    assert profile == PlantProfile(
        sensor_id=sid,
        plant_id=None,
        sensor_name="Prof",
        avg_absorption_per_minute=2.875,
        avg_drainage_per_hour=-3.0,
        response_count=2,
        min_delta=1.5,
        max_delta=20.0,
        efficiency_score=0.5,
    )


@pytest.mark.parametrize(("lead", "found"), [(1800, True), (2400, False)])
def test_pre_irrigation_window_is_thirty_minutes(repo, lead, found):
    """learning-24: the "pre" sample must lie within 30 min before the start."""
    sid = _probe(repo)
    start = FROZEN_TS - 3 * HOUR
    repo.add_sensor_reading(sensor_id=sid, timestamp=start - lead, soil_moisture=30.0)
    repo.add_sensor_reading(sensor_id=sid, timestamp=start + 1200, soil_moisture=50.0)
    response = compute_sensor_response(
        repo, repo.get_sensor(sid), SimpleNamespace(id=1, timestamp=start, duration_minutes=2)
    )
    assert (response is not None) is found


@pytest.mark.parametrize(("efficiency", "warned"), [(0.5, False), (0.55, False), (0.49, True)])
def test_learning_report_text(repo, monkeypatch, efficiency, warned):
    """learning-26 / learning-27: the report flags efficiency < 0.5 and upper-cases alert severities."""
    sid = _probe(repo)
    cid = repo.get_sensor(sid).cluster_id
    monkeypatch.setattr(
        report_mod, "get_plant_profile", lambda db, sensor: _profile(sensor.id, efficiency=efficiency, absorption=1.0)
    )
    monkeypatch.setattr(
        report_mod, "detect_issues", lambda db, pdb, c: [Alert(severity="warning", alert_type="x", message="check it")]
    )
    lines = report_mod.generate_report(repo, plant_database(), cid).split("\n")
    assert lines[:3] == ["📊 Irrigation Learning Report", "=" * 40, ""]
    assert lines[3] == "🌱 Repo Probe"
    assert lines[-2:] == ["🚨 Alerts", "   [WARNING] check it"]
    assert ("   ⚠️ Low efficiency — check drip positioning" in lines) is warned


def test_conflict_projection_skips_a_dry_plant_with_zero_absorption(repo):
    """learning-15: a dry sensor whose learned absorption is ≤ 0 is skipped (no division by zero)."""
    cid, a, b = _two_probe_cluster(repo, link_plant=False)
    repo.add_sensor_reading(sensor_id=a, timestamp=FROZEN_TS - 600, soil_moisture=30.0)  # dry vs default 45
    repo.add_sensor_reading(sensor_id=b, timestamp=FROZEN_TS - 600, soil_moisture=80.0)  # wet vs default 65
    profiles = {a: _profile(a, efficiency=1.0, absorption=0.0), b: _profile(b, efficiency=1.0, absorption=1.0)}
    assert issues_mod.detect_conflicts(repo, plant_database(), cid, profiles, {}) == []
