"""CLAUDE.md "Key invariants" pinned at the decision-engine level (characterization).

One clearly named test (or small group) per invariant; each docstring cites the
invariant number. Invariants already pinned *exactly* elsewhere are only cited
here. Clock frozen at
``golden.FROZEN_INSTANT`` (Wed 2026-04-15 10:00 UTC) via ``frozen_clock``;
every timestamp is derived from it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import time_machine
from sqlalchemy import func, select

import greenhouse_core.constants as constants_mod
import greenhouse_core.logic.engine as engine_mod
from engine_grid import FakeForecastWeather, plant_database
from golden import FROZEN_TS
from greenhouse_core.constants import LEAK_ALERT_CODE, LEAK_HOLD_HOURS, MIN_COOLDOWN_HOURS
from greenhouse_core.learning import Alert, IrrigationLearner
from greenhouse_core.logic import IrrigationDecision, IrrigationLogic
from greenhouse_core.logic.decision import Action, Reason, TriggerCode
from greenhouse_core.models import DecisionLog, IrrigationEvent, SensorReading

HOUR = 3600


@pytest.fixture
def env(tmp_db, frozen_clock, clean_env):
    return tmp_db


def _cluster(db, *, soils=(38.0,), species="Monstera deliciosa", category="tropical", environment="indoor"):
    """One plant, one irrigator, one sensor per soil value (one reading each, 10 min old)."""
    cid = db.add_cluster("Invariant Cluster", environment=environment)
    db.add_plant(cluster_id=cid, species=species, category=category)
    irr = db.add_irrigator(
        cluster_id=cid,
        tuya_device_id="fake_tuya_device_inv0001",
        name="Pump",
        irrigator_type="rainpoint.ik10pw",
        config={},
    )
    sensors = []
    for i, soil in enumerate(soils):
        sid = db.add_sensor(
            cluster_id=cid,
            tuya_device_id=f"fake_tuya_sensor_inv{i:04d}",
            name=f"Soil {chr(65 + i)}",
            sensor_type="tuya.tr301z",
            config={},
        )
        db.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=soil)
        sensors.append(sid)
    return cid, irr, sensors


def _logic(db, **kw):
    return IrrigationLogic(db, plant_database(), **kw)


def _codes(decision):
    return [r.code for r in decision.reasons]


def _raise_leak(db, cluster_id, *, age_s=HOUR):
    return db.upsert_alert(
        dedup_key=f"leak::{LEAK_ALERT_CODE}::{cluster_id}::sensor1",
        source="leak",
        code=LEAK_ALERT_CODE,
        title="Possible leak or stuck valve detected",
        message="Soil A: soil moisture still rising after irrigation",
        severity="critical",
        entity_type="sensor",
        entity_id=1,
        cluster_id=cluster_id,
        seen_at=FROZEN_TS - age_s,
    )


# ── #2 Driest plant drives the call ──────────────────────────────────────────


def test_inv2_driest_plant_drives_the_call_not_the_average(env):
    """Invariant #2: ``min_soil_moisture`` decides, even when the average is adequate.

    Monstera target 45-60. Sensors 50/51/52/53/33 → average 47.8 (adequate), wettest
    53 (below the conflict band >55), but the driest (33) is below target -
    VERY_DRY_MARGIN → SENSOR_VERY_DRY, IRRIGATE.
    """
    cid, _, _ = _cluster(env, soils=(50.0, 51.0, 52.0, 53.0, 33.0))

    decision = _logic(env).decide_for_cluster(cid)

    assert decision.sensor_snapshot.avg_soil_moisture == pytest.approx(47.8)
    assert decision.sensor_snapshot.min_soil_moisture == 33.0
    assert decision.action is Action.IRRIGATE
    assert decision.primary_code is TriggerCode.SENSOR_VERY_DRY
    assert "driest=33%" in decision.reasons[0].message


def test_inv2_stress_rule_keys_on_average_current_behavior(env):
    """Invariant #2 caveat: critical-stress detection keys on the **average** (stress.py).

    Pins current behavior: sensors 10/60 → average 35 (not < SOIL_MOISTURE_CRITICAL=30),
    so no WATER_STRESS even though one plant is at 10%; the soil rule then sees
    min 10 / max 60 → CONFLICT short burst (not a stress irrigation).
    """
    cid, _, _ = _cluster(env, soils=(10.0, 60.0))

    decision = _logic(env).decide_for_cluster(cid)

    assert decision.stress_indicators.water_stress is None
    assert decision.primary_code is TriggerCode.CONFLICT
    assert decision.duration_minutes == 1


# ── #3 6h cooldown; only action == "start" counts ────────────────────────────


@pytest.mark.parametrize(
    ("age_s", "expect_cooldown"),
    [
        (MIN_COOLDOWN_HOURS * HOUR - 60, True),
        (MIN_COOLDOWN_HOURS * HOUR - 1, True),
        (MIN_COOLDOWN_HOURS * HOUR, True),  # boundary is inclusive (`timestamp >= now - 6h`)
        (MIN_COOLDOWN_HOURS * HOUR + 1, False),
        (MIN_COOLDOWN_HOURS * HOUR + 60, False),
    ],
)
def test_inv3_cooldown_boundary_is_six_hours_inclusive(env, age_s, expect_cooldown):
    """Invariant #3: a ``start`` within ``MIN_COOLDOWN_HOURS`` (6h, inclusive) skips with COOLDOWN."""
    cid, irr, _ = _cluster(env)
    env.add_irrigation_event(irr, "start", "auto", duration_minutes=2, timestamp=FROZEN_TS - age_s)

    decision = _logic(env).decide_for_cluster(cid)

    if expect_cooldown:
        assert decision.action is Action.SKIP
        assert _codes(decision) == [TriggerCode.COOLDOWN]
        assert decision.interval_hours == MIN_COOLDOWN_HOURS
    else:
        assert decision.action is Action.IRRIGATE
        assert TriggerCode.COOLDOWN not in _codes(decision)


def test_inv3_manual_start_counts_towards_cooldown(env):
    """Invariant #3: the cooldown is global across ``triggered_by`` — a manual start also blocks."""
    cid, irr, _ = _cluster(env)
    env.add_irrigation_event(irr, "start", "manual", duration_minutes=2, timestamp=FROZEN_TS - 2 * HOUR)

    decision = _logic(env).decide_for_cluster(cid)

    assert decision.primary_code is TriggerCode.COOLDOWN
    assert "trigger: manual" in decision.reasons[0].message


@pytest.mark.parametrize("action", ["schedule_updated", "stop", "dry_run"])
def test_inv3_only_start_events_count_towards_cooldown(env, action):
    """Invariant #3 (+ #11 / issue #103): non-``start`` events never block irrigation."""
    cid, irr, _ = _cluster(env)
    env.add_irrigation_event(irr, action, "auto", duration_minutes=2, timestamp=FROZEN_TS - HOUR)

    decision = _logic(env).decide_for_cluster(cid)

    assert decision.action is Action.IRRIGATE
    assert TriggerCode.COOLDOWN not in _codes(decision)


def test_inv3_cooldown_is_not_bypassed_by_manual_override(env):
    """Invariant #3: ``bypass_quiet_hours`` (force) does not bypass the cooldown gate."""
    cid, irr, _ = _cluster(env)
    env.add_irrigation_event(irr, "start", "auto", duration_minutes=2, timestamp=FROZEN_TS - HOUR)

    decision = _logic(env).decide_for_cluster(cid, bypass_quiet_hours=True, triggered_by="manual")

    assert decision.primary_code is TriggerCode.COOLDOWN


# ── #4 Learning is advisory ──────────────────────────────────────────────────


def _decision_core(decision: IrrigationDecision) -> dict:
    dump = decision.model_dump(mode="json")
    dump["stress_indicators"].pop("learning_alerts")
    return dump


@pytest.mark.parametrize("soils", [(38.0,), (55.0,), (20.0,), (10.0, 60.0), (75.0,)])
def test_inv4_learning_failure_or_alerts_never_change_the_decision(env, monkeypatch, soils):
    """Invariant #4: a learner that raises, returns ``[]`` or returns alerts yields the same decision.

    Only ``stress_indicators.learning_alerts`` differs; action, dosage, confidence and
    the reason trail (codes + order) are identical.
    """
    cid, _, _ = _cluster(env, soils=soils)
    alert = Alert(severity="critical", alert_type="blocked_drip", message="drip blocked")
    results = {}
    for mode in ("empty", "raises", "alerts"):

        def fake_detect(self, cluster_id, _mode=mode):
            if _mode == "raises":
                raise RuntimeError("learner exploded")
            return [alert] if _mode == "alerts" else []

        monkeypatch.setattr(IrrigationLearner, "detect_issues", fake_detect)
        results[mode] = _logic(env).decide_for_cluster(cid)

    assert _decision_core(results["raises"]) == _decision_core(results["empty"])
    assert _decision_core(results["alerts"]) == _decision_core(results["empty"])
    assert results["raises"].stress_indicators.learning_alerts == []
    assert results["alerts"].stress_indicators.learning_alerts == [
        {"type": "blocked_drip", "severity": "critical", "message": "drip blocked"}
    ]
    assert TriggerCode.LEARNING_ALERT not in _codes(results["alerts"])


def test_inv4_learner_is_consulted_once_per_sensor_path_evaluation(env, monkeypatch):
    """Invariant #4: the engine calls the learner exactly once per (non-terminal) evaluation."""
    cid, _, _ = _cluster(env)
    calls = []
    monkeypatch.setattr(IrrigationLearner, "detect_issues", lambda self, cluster_id: calls.append(cluster_id) or [])

    _logic(env).decide_for_cluster(cid)

    assert calls == [cid]


# ── #5 Thresholds live in constants.py ───────────────────────────────────────


def test_inv5_engine_constants_mirror_constants_module(env):
    """Invariant #5: every UPPER_CASE name the engine imports is the constants.py value."""
    imported = {n for n in vars(engine_mod) if n.isupper() and hasattr(constants_mod, n)}

    assert len(imported) >= 50
    for name in imported:
        assert getattr(engine_mod, name) == getattr(constants_mod, name), name


def test_inv5_thresholds_are_bound_at_import_current_behavior(env, monkeypatch):
    """Invariant #5: the engine reads thresholds via its *own* module globals.

    Pins current behavior: ``from constants import MIN_COOLDOWN_HOURS`` binds the
    value at import, so patching ``greenhouse_core.constants`` does nothing, while
    patching ``greenhouse_core.logic.engine.MIN_COOLDOWN_HOURS`` moves the decision
    (read at call time). A refactor that switches to ``constants.X`` attribute
    access would flip the first assertion — that is a behavior change for tests.
    """
    cid, irr, _ = _cluster(env)
    env.add_irrigation_event(irr, "start", "auto", duration_minutes=2, timestamp=FROZEN_TS - 3 * HOUR)

    monkeypatch.setattr(constants_mod, "MIN_COOLDOWN_HOURS", 2)
    assert _logic(env).decide_for_cluster(cid).primary_code is TriggerCode.COOLDOWN

    monkeypatch.setattr(engine_mod, "MIN_COOLDOWN_HOURS", 2)
    decision = _logic(env).decide_for_cluster(cid)
    assert decision.action is Action.IRRIGATE
    assert TriggerCode.COOLDOWN not in _codes(decision)


def test_inv5_inline_literals_current_behavior(env):
    """Invariant #5 gaps: literals still inline in engine.py are pinned here.

    Forecast horizon ``hours=6``, rain threshold ``> 2.0`` mm, base confidence ``0.5``.
    """
    cid, _, _ = _cluster(env, environment="outdoor")
    weather = FakeForecastWeather({"precipitation_mm": 2.0})

    decision = _logic(env, weather_client=weather).decide_for_cluster(cid)

    assert weather.forecast_calls == [6]
    assert decision.primary_code is TriggerCode.SENSOR_DRY  # 2.0 mm is not "> 2.0"
    weather_rain = FakeForecastWeather({"precipitation_mm": 2.0001})
    assert _logic(env, weather_client=weather_rain).decide_for_cluster(cid).primary_code is TriggerCode.WEATHER_SKIP

    temp_only_cid = env.add_cluster("Temp only")
    env.add_plant(cluster_id=temp_only_cid, species="Monstera deliciosa", category="tropical")
    sid = env.add_sensor(
        cluster_id=temp_only_cid, tuya_device_id="fake_tuya_sensor_inv9999", name="T", sensor_type="x", config={}
    )
    env.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, temperature=22.0)
    no_reason = _logic(env).decide_for_cluster(temp_only_cid)
    assert (no_reason.action, no_reason.confidence, no_reason.reasons) == (Action.SKIP, 0.5, [])
    assert no_reason.reason_text == "no specific conditions"


# ── #6 One DecisionLog per evaluation ────────────────────────────────────────


def _log_rows(db):
    return list(db.session.scalars(select(DecisionLog).order_by(DecisionLog.id)))


def _scenario(db, name):
    """Build one cluster per engine exit branch; returns ``(cluster_id, logic, kwargs)``."""
    if name == "no_plants":
        return db.add_cluster("Empty"), _logic(db), {}
    if name == "leak_hold":
        cid, _, _ = _cluster(db)
        _raise_leak(db, cid)
        return cid, _logic(db), {}
    if name == "cooldown":
        cid, irr, _ = _cluster(db)
        db.add_irrigation_event(irr, "start", "auto", duration_minutes=2, timestamp=FROZEN_TS - HOUR)
        return cid, _logic(db), {}
    if name == "quiet_hours":
        cid, _, _ = _cluster(db)
        db.update_global_irrigation_config(quiet_start_hour=9, quiet_end_hour=11)
        return cid, _logic(db), {}
    if name == "weather_skip":
        cid, _, _ = _cluster(db, environment="outdoor")
        return cid, _logic(db, weather_client=FakeForecastWeather({"precipitation_mm": 9.0})), {}
    if name == "temp_fallback":
        cid = db.add_cluster("No sensors")
        db.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical")
        return cid, _logic(db), {"current_temp": 30.0}
    if name == "water_warning":
        cid, _, _ = _cluster(db, soils=())
        sid = db.add_sensor(
            cluster_id=cid, tuya_device_id="fake_tuya_sensor_invww", name="WW", sensor_type="x", config={}
        )
        db.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=50.0, water_warning=True)
        return cid, _logic(db), {}
    if name == "water_stress":
        cid, _, _ = _cluster(db, soils=(20.0,))
        return cid, _logic(db), {}
    if name == "outside_window":
        cid, _, _ = _cluster(db)
        db.add_irrigation_window(cid, start_hour=6, end_hour=8)
        return cid, _logic(db), {}
    if name == "sensor_dry":
        cid, _, _ = _cluster(db)
        return cid, _logic(db), {}
    if name == "sensor_adequate":
        cid, _, _ = _cluster(db, soils=(55.0,))
        return cid, _logic(db), {}
    raise ValueError(name)


EXIT_BRANCHES = [
    ("no_plants", TriggerCode.NO_PLANTS, Action.SKIP),
    ("leak_hold", TriggerCode.LEAK_HOLD, Action.SKIP),
    ("cooldown", TriggerCode.COOLDOWN, Action.SKIP),
    ("quiet_hours", TriggerCode.QUIET_HOURS, Action.SKIP),
    ("weather_skip", TriggerCode.WEATHER_SKIP, Action.SKIP),
    ("temp_fallback", TriggerCode.TEMP_FALLBACK, Action.IRRIGATE),
    ("water_warning", TriggerCode.WATER_WARNING, Action.IRRIGATE),
    ("water_stress", TriggerCode.WATER_STRESS, Action.IRRIGATE),
    ("outside_window", TriggerCode.OUTSIDE_WINDOW, Action.SKIP),
    ("sensor_dry", TriggerCode.SENSOR_DRY, Action.IRRIGATE),
    ("sensor_adequate", TriggerCode.SENSOR_ADEQUATE, Action.SKIP),
]


@pytest.mark.parametrize(("name", "code", "action"), EXIT_BRANCHES, ids=[b[0] for b in EXIT_BRANCHES])
def test_inv6_every_exit_branch_persists_exactly_one_decision_log(env, name, code, action):
    """Invariant #6: every evaluation (acted-on or not) writes one ``decision_logs`` row with persist=True.

    The row mirrors the decision (primary_code, reason_text, action, dosage), is
    ``actuated=False`` (only the service flips it after a real start), and its
    ``payload_json`` round-trips to the same ``IrrigationDecision``.
    """
    cid, logic, kwargs = _scenario(env, name)

    decision = logic.decide_for_cluster(cid, persist=True, triggered_by="scheduler", **kwargs)

    rows = _log_rows(env)
    assert len(rows) == 1
    row = rows[0]
    assert decision.primary_code is code and decision.action is action
    assert row.id == decision.decision_log_id
    assert (row.cluster_id, row.evaluated_at, row.triggered_by) == (cid, FROZEN_TS, "scheduler")
    assert (row.action, row.primary_code, row.reason_text) == (action.value, code.value, decision.reason_text)
    assert (row.duration_minutes, row.interval_hours, row.confidence) == (
        decision.duration_minutes,
        decision.interval_hours,
        decision.confidence,
    )
    assert row.actuated is False
    restored = IrrigationDecision.model_validate(json.loads(row.payload_json))
    assert restored.model_dump(mode="json") == decision.model_dump(mode="json")
    assert "decision_log_id" not in json.loads(row.payload_json)


@pytest.mark.parametrize(("name", "code", "action"), EXIT_BRANCHES, ids=[b[0] for b in EXIT_BRANCHES])
def test_inv6_persist_false_writes_nothing(env, name, code, action):
    """Invariant #6 scope: persistence is opt-in (``persist=True``); the default writes no row."""
    cid, logic, kwargs = _scenario(env, name)

    decision = logic.decide_for_cluster(cid, **kwargs)

    assert decision.primary_code is code
    assert decision.decision_log_id is None
    assert _log_rows(env) == []


def test_inv6_unknown_cluster_returns_none_and_writes_nothing(env):
    """Invariant #6 scope: an unknown cluster is not an evaluation — ``None``, no row."""
    assert _logic(env).decide_for_cluster(424242, persist=True) is None
    assert _log_rows(env) == []


def test_inv6_persistence_failure_never_blocks_the_decision(env, monkeypatch):
    """Invariant #6: ``_persist`` is best-effort — a failing write still returns the decision."""
    cid, _, _ = _cluster(env)

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(env, "add_decision_log", boom)
    decision = _logic(env).decide_for_cluster(cid, persist=True)

    assert decision.action is Action.IRRIGATE
    assert decision.decision_log_id is None


def test_inv6_reasons_stay_a_list_of_reason_through_the_whole_pipeline(env):
    """Invariant #6: ``reasons`` is ``list[Reason]`` on a decision that ran every mutating rule.

    (Assignment coercion itself is pinned by
    ``test_engine_timing.py::TestDecisionAssignmentValidation``.)
    """
    cid, _, _ = _cluster(env, soils=(38.0,), species="Unknown fern", category="fern")
    env.add_vacation_window(starts_at=FROZEN_TS - HOUR, ends_at=FROZEN_TS + 3 * 86400)

    decision = _logic(env).decide_for_cluster(cid)

    assert IrrigationDecision.model_config["validate_assignment"] is True
    assert type(decision.reasons) is list
    assert all(type(r) is Reason for r in decision.reasons)
    assert _codes(decision) == [TriggerCode.SENSOR_VERY_DRY, TriggerCode.WATER_NEEDS_HIGH, TriggerCode.VACATION_ACTIVE]


# ── #9 Plant-DB timing precedence ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("species", "category", "environment", "at_month", "expected"),
    [
        # species-level indoor multiplier (Alocasia winter 0.4) beats tropical category (0.5)
        ("Alocasia amazonica", "tropical", "indoor", 1, "winter multiplier 0.4×"),
        # no species timing → tropical _category_defaults (summer 1.2 == built-in, autumn 0.8)
        ("Monstera deliciosa", "tropical", "indoor", 10, "autumn multiplier 0.8×"),
        # species _outdoor key (Loquat summer 1.5) beats category _outdoor (1.6)
        ("Eriobotrya japonica", "fruit_tree", "outdoor", 7, "summer multiplier 1.5×"),
        # Loquat has no indoor key → category fruit_tree indoor (summer 1.5)
        ("Eriobotrya japonica", "fruit_tree", "indoor", 7, "summer multiplier 1.5×"),
        # unknown species → category fruit_tree _outdoor (summer 1.6)
        ("Unknown fruit tree", "fruit_tree", "outdoor", 7, "summer multiplier 1.6×"),
        # fern has no _outdoor key → falls to built-in outdoor table (winter 0.3), NOT fern indoor (0.7)
        ("Unknown fern", "fern", "outdoor", 1, "winter multiplier 0.3×"),
        # no species, no category → built-in indoor table (winter 0.5)
        ("Plantus fictitius", None, "indoor", 1, "winter multiplier 0.5×"),
    ],
)
def test_inv9_seasonal_multiplier_precedence_species_category_constants(
    tmp_db, clean_env, species, category, environment, at_month, expected
):
    """Invariant #9: species > ``_category_defaults`` > constants; outdoor uses ``*_outdoor`` (no indoor fallback)."""
    at = datetime(2026, at_month, 15, 12, 0, 0, tzinfo=UTC)
    with time_machine.travel(at, tick=False):
        ts = int(at.timestamp())
        cid = tmp_db.add_cluster("Season", environment=environment)
        tmp_db.add_plant(cluster_id=cid, species=species, category=category)
        sid = tmp_db.add_sensor(
            cluster_id=cid, tuya_device_id="fake_tuya_sensor_inv_s", name="S", sensor_type="x", config={}
        )
        tmp_db.add_sensor_reading(sensor_id=sid, timestamp=ts - 600, soil_moisture=50.0)

        decision = _logic(tmp_db).decide_for_cluster(cid)

    seasonal = [r for r in decision.reasons if r.code in (TriggerCode.SEASONAL_HOLD, TriggerCode.SEASONAL_BOOST)]
    assert len(seasonal) == 1
    assert seasonal[0].message.startswith(expected)
    assert seasonal[0].message.endswith(f"for {environment} cluster")


def test_inv9_preferred_water_hours_never_gate_without_window_rows(env):
    """Invariant #9: no ``IrrigationWindow`` rows → all hours allowed (issue #83).

    Loquat prefers 05-09 local; at 10:00 UTC with no windows it still irrigates,
    and adding a row that excludes 10:00 is what produces OUTSIDE_WINDOW.
    """
    cid, _, _ = _cluster(env, species="Eriobotrya japonica", category="fruit_tree")

    assert _logic(env).decide_for_cluster(cid).action is Action.IRRIGATE

    env.add_irrigation_window(cid, start_hour=5, end_hour=9)
    assert _logic(env).decide_for_cluster(cid).primary_code is TriggerCode.OUTSIDE_WINDOW


# ── #10 Cleaned view for judgements, raw rows for the archive ────────────────


def test_inv10_spike_is_ignored_by_the_decision_and_raw_rows_are_untouched(env):
    """Invariant #10: a Hampel-rejected spike does not drive the call; ``sensor_readings`` is never mutated."""
    cid = env.add_cluster("Spike")
    env.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical")
    sid = env.add_sensor(cluster_id=cid, tuya_device_id="fake_tuya_sensor_spk", name="S", sensor_type="x", config={})
    series = [55.0, 55.0, 55.0, 55.0, 10.0, 55.0, 55.0, 55.0, 55.0]
    for i, soil in enumerate(series):
        env.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - (len(series) - 1 - i) * HOUR, soil_moisture=soil)
    before = [(r.id, r.timestamp, r.soil_moisture) for r in env.session.scalars(select(SensorReading))]

    decision = _logic(env).decide_for_cluster(cid, persist=True)

    assert decision.sensor_snapshot.min_soil_moisture == 55.0
    assert decision.primary_code is TriggerCode.SENSOR_ADEQUATE
    after = [(r.id, r.timestamp, r.soil_moisture) for r in env.session.scalars(select(SensorReading))]
    assert after == before
    assert 10.0 in [soil for _, _, soil in after]


def test_inv10_short_series_spike_is_not_filtered_current_behavior(env):
    """Invariant #10 edge: below ``CLEANING_HAMPEL_MIN_READINGS`` (5) the spike filter is off.

    Pins current behavior: four readings 55/55/10/55 → the 10 survives and, being
    the driest value, drives SENSOR_VERY_DRY (invariant #2 on raw-ish data).
    """
    cid = env.add_cluster("Short")
    env.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical")
    sid = env.add_sensor(cluster_id=cid, tuya_device_id="fake_tuya_sensor_sht", name="S", sensor_type="x", config={})
    for i, soil in enumerate([55.0, 55.0, 10.0, 55.0]):
        env.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - (3 - i) * HOUR, soil_moisture=soil)

    decision = _logic(env).decide_for_cluster(cid)

    assert decision.sensor_snapshot.min_soil_moisture == 10.0
    assert decision.primary_code is TriggerCode.SENSOR_VERY_DRY


# ── #11 A leak alert IS the hold ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("age_s", "held"),
    [(LEAK_HOLD_HOURS * HOUR, True), (LEAK_HOLD_HOURS * HOUR + 1, False)],
    ids=["exactly-LEAK_HOLD_HOURS", "LEAK_HOLD_HOURS+1s"],
)
def test_inv11_hold_window_edge_is_inclusive(env, age_s, held):
    """Invariant #11: the hold lasts ``LEAK_HOLD_HOURS`` from ``last_seen_at``, inclusive at the edge.

    (±60 s / ±600 s, acknowledge-still-holds, resolve-releases, force-does-not-bypass
    are pinned in ``tests/test_leak_hold.py``.)
    """
    cid, _, _ = _cluster(env)
    _raise_leak(env, cid, age_s=age_s)

    decision = _logic(env).decide_for_cluster(cid)

    if held:
        assert decision.primary_code is TriggerCode.LEAK_HOLD
        assert "(0.0h left)" in decision.reasons[0].message
        assert (decision.duration_minutes, decision.interval_hours) == (0, LEAK_HOLD_HOURS)
    else:
        assert decision.action is Action.IRRIGATE


def test_inv11_hold_is_never_written_as_an_irrigation_event(env):
    """Invariant #11: the hold lives in the alert inbox only — no ``IrrigationEvent`` is written.

    Also: acknowledging keeps the hold, ``bypass_quiet_hours`` does not bypass it,
    resolving releases it — evaluated in sequence on one cluster.
    """
    cid, _, _ = _cluster(env)
    alert = _raise_leak(env, cid)
    logic = _logic(env)

    assert logic.decide_for_cluster(cid, persist=True).primary_code is TriggerCode.LEAK_HOLD
    env.acknowledge_alert(alert.id)
    assert logic.decide_for_cluster(cid, persist=True).primary_code is TriggerCode.LEAK_HOLD
    forced = logic.decide_for_cluster(cid, persist=True, bypass_quiet_hours=True, triggered_by="manual")
    assert forced.primary_code is TriggerCode.LEAK_HOLD
    env.resolve_alert(alert.id)
    assert logic.decide_for_cluster(cid, persist=True).action is Action.IRRIGATE

    assert env.session.scalar(select(func.count()).select_from(IrrigationEvent)) == 0
    assert [r.primary_code for r in _log_rows(env)] == ["leak_hold", "leak_hold", "leak_hold", "sensor_dry"]
