"""AGENTS.md "Key invariants" pinned at the decision-engine level (characterization).

One clearly named test (or small group) per invariant; each docstring cites the
invariant number. Invariants already pinned *exactly* elsewhere are only cited
here. Clock frozen at
``golden.FROZEN_INSTANT`` (Wed 2026-04-15 10:00 UTC) via ``frozen_clock``;
every timestamp is derived from it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Never, TypedDict, cast

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
from greenhouse_core.models import (
    ActivitySource,
    DecisionLog,
    EntityType,
    EventAction,
    IrrigationEvent,
    SensorReading,
    TriggeredBy,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from greenhouse_core.logic.engine import RainForecast
    from greenhouse_core.models import Alert as AlertRow
    from greenhouse_core.repository import IrrigationRepository

DRIEST_SOIL = 33.0  # invariant 2 scenario: the driest of five sensors
HEALTHY_SOIL = 55.0  # invariant 10 scenarios: the steady level around the spike
SPIKE_SOIL = 10.0
MIN_ENGINE_CONSTANTS = 50  # the engine imports at least this many names from constants.py

HOUR = 3600


@pytest.fixture
def env(tmp_db: IrrigationRepository, frozen_clock: time_machine.Traveller, clean_env: Path) -> IrrigationRepository:
    """Provide the test database with the clock frozen and the environment cleared."""
    del frozen_clock, clean_env  # side-effect fixtures (the clock is frozen; a hermetic environment)
    return tmp_db


def _cluster(
    db: IrrigationRepository,
    *,
    soils: tuple[float, ...] = (38.0,),
    species: str = "Monstera deliciosa",
    category: str | None = "tropical",
    environment: str = "indoor",
) -> tuple[int, int, list[int]]:
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
    sensors: list[int] = []
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


def _logic(db: IrrigationRepository, *, weather_client: RainForecast | None = None) -> IrrigationLogic:
    return IrrigationLogic(db, plant_database(), weather_client=weather_client)


def _decided(decision: IrrigationDecision | None) -> IrrigationDecision:
    """The decision for a cluster that exists (``decide_for_cluster`` returns None only for an unknown one)."""
    assert decision is not None
    return decision


def _codes(decision: IrrigationDecision) -> list[TriggerCode]:
    return [r.code for r in decision.reasons]


def _raise_leak(db: IrrigationRepository, cluster_id: int, *, age_s: int = HOUR) -> AlertRow:
    return db.upsert_alert(
        dedup_key=f"leak::{LEAK_ALERT_CODE}::{cluster_id}::sensor1",
        source=ActivitySource.LEAK,
        code=LEAK_ALERT_CODE,
        title="Possible leak or stuck valve detected",
        message="Soil A: soil moisture still rising after irrigation",
        severity="critical",
        entity_type=EntityType.SENSOR,
        entity_id=1,
        cluster_id=cluster_id,
        seen_at=FROZEN_TS - age_s,
    )


# ── #2 Driest plant drives the call ──────────────────────────────────────────


def test_inv2_driest_plant_drives_the_call_not_the_average(env: IrrigationRepository) -> None:
    """Invariant #2: ``min_soil_moisture`` decides, even when the average is adequate.

    Monstera target 45-60. Sensors 50/51/52/53/33 → average 47.8 (adequate), wettest
    53 (below the conflict band >55), but the driest (33) is below target -
    VERY_DRY_MARGIN → SENSOR_VERY_DRY, IRRIGATE.
    """
    cid, _, _ = _cluster(env, soils=(50.0, 51.0, 52.0, 53.0, 33.0))

    decision = _logic(env).decide_for_cluster(cid)
    assert decision is not None

    assert decision.sensor_snapshot is not None
    assert decision.sensor_snapshot.avg_soil_moisture == pytest.approx(47.8)
    assert decision.sensor_snapshot.min_soil_moisture == DRIEST_SOIL
    assert decision.action is Action.IRRIGATE
    assert decision.primary_code is TriggerCode.SENSOR_VERY_DRY
    assert "driest=33%" in decision.reasons[0].message


def test_inv2_stress_rule_keys_on_the_driest_plant(env: IrrigationRepository) -> None:
    """Invariant #2 holds for critical stress too: the **driest** plant, not the average, decides.

    Sensors 10/60 → average 35 (not < SOIL_MOISTURE_CRITICAL=30), but the driest
    plant sits at 10 %, so WATER_STRESS fires and the stress dosage wins over the
    CONFLICT short burst the soil rule would otherwise pick.
    """
    cid, _, _ = _cluster(env, soils=(10.0, 60.0))

    decision = _logic(env).decide_for_cluster(cid)
    assert decision is not None

    assert decision.sensor_snapshot is not None
    assert decision.sensor_snapshot.avg_soil_moisture == pytest.approx(35.0)
    assert decision.stress_indicators.water_stress == "critical low (10%)"
    assert decision.primary_code is TriggerCode.WATER_STRESS
    assert (decision.action, decision.duration_minutes) == (Action.IRRIGATE, constants_mod.STRESS_DURATION_MINUTES)


# ── #3 6h cooldown; only action == "start" counts ────────────────────────────


@pytest.mark.parametrize(
    ("age_s", "expect_cooldown"),
    [
        (MIN_COOLDOWN_HOURS * HOUR - 60, True),
        (MIN_COOLDOWN_HOURS * HOUR - 1, True),
        (MIN_COOLDOWN_HOURS * HOUR, False),  # boundary is exclusive: exactly 6 h later the cooldown is over
        (MIN_COOLDOWN_HOURS * HOUR + 1, False),
        (MIN_COOLDOWN_HOURS * HOUR + 60, False),
    ],
)
def test_inv3_cooldown_boundary_is_six_hours_exclusive(
    env: IrrigationRepository, age_s: int, *, expect_cooldown: bool
) -> None:
    """Invariant #3: a ``start`` less than ``MIN_COOLDOWN_HOURS`` (6h) ago skips with COOLDOWN.

    At 6h it no longer does: the boundary is exclusive.
    """
    cid, irr, _ = _cluster(env)
    env.add_irrigation_event(irr, EventAction.START, TriggeredBy.AUTO, duration_minutes=2, timestamp=FROZEN_TS - age_s)

    decision = _logic(env).decide_for_cluster(cid)
    assert decision is not None

    if expect_cooldown:
        assert decision.action is Action.SKIP
        assert _codes(decision) == [TriggerCode.COOLDOWN]
        assert decision.interval_hours == MIN_COOLDOWN_HOURS
    else:
        assert decision.action is Action.IRRIGATE
        assert TriggerCode.COOLDOWN not in _codes(decision)


def test_inv3_manual_start_counts_towards_cooldown(env: IrrigationRepository) -> None:
    """Invariant #3: the cooldown is global across ``triggered_by`` — a manual start also blocks."""
    cid, irr, _ = _cluster(env)
    env.add_irrigation_event(
        irr, EventAction.START, TriggeredBy.MANUAL, duration_minutes=2, timestamp=FROZEN_TS - 2 * HOUR
    )

    decision = _logic(env).decide_for_cluster(cid)
    assert decision is not None

    assert decision.primary_code is TriggerCode.COOLDOWN
    assert "trigger: manual" in decision.reasons[0].message


@pytest.mark.parametrize("action", ["schedule_updated", "stop", "dry_run"])
def test_inv3_only_start_events_count_towards_cooldown(env: IrrigationRepository, action: str) -> None:
    """Invariant #3 (+ #11 / issue #103): non-``start`` events never block irrigation."""
    cid, irr, _ = _cluster(env)
    # Actions outside EventAction on purpose (schedule_updated, dry_run): only "start" may count.
    env.add_irrigation_event(
        irr, cast("EventAction", action), TriggeredBy.AUTO, duration_minutes=2, timestamp=FROZEN_TS - HOUR
    )

    decision = _logic(env).decide_for_cluster(cid)
    assert decision is not None

    assert decision.action is Action.IRRIGATE
    assert TriggerCode.COOLDOWN not in _codes(decision)


def test_inv3_cooldown_is_not_bypassed_by_manual_override(env: IrrigationRepository) -> None:
    """Invariant #3: ``bypass_quiet_hours`` (force) does not bypass the cooldown gate."""
    cid, irr, _ = _cluster(env)
    env.add_irrigation_event(irr, EventAction.START, TriggeredBy.AUTO, duration_minutes=2, timestamp=FROZEN_TS - HOUR)

    decision = _logic(env).decide_for_cluster(cid, bypass_quiet_hours=True, triggered_by=TriggeredBy.MANUAL)
    assert decision is not None

    assert decision.primary_code is TriggerCode.COOLDOWN


# ── #4 Learning is advisory ──────────────────────────────────────────────────


def _decision_core(decision: IrrigationDecision) -> dict[str, Any]:
    dump = decision.model_dump(mode="json")
    dump["stress_indicators"].pop("learning_alerts")
    return dump


@pytest.mark.parametrize("soils", [(38.0,), (55.0,), (20.0,), (10.0, 60.0), (75.0,)])
def test_inv4_learning_failure_or_alerts_never_change_the_decision(
    env: IrrigationRepository, monkeypatch: pytest.MonkeyPatch, soils: tuple[float, ...]
) -> None:
    """Invariant #4: a learner that raises, returns ``[]`` or returns alerts yields the same decision.

    Only ``stress_indicators.learning_alerts`` differs; action, dosage, confidence and
    the reason trail (codes + order) are identical.
    """
    cid, _, _ = _cluster(env, soils=soils)
    alert = Alert(severity="critical", alert_type="blocked_drip", message="drip blocked")
    results: dict[str, IrrigationDecision] = {}
    for mode in ("empty", "raises", "alerts"):

        def fake_detect(_self: IrrigationLearner, _cluster_id: int, _mode: str = mode) -> list[Alert]:
            if _mode == "raises":
                msg = "learner exploded"
                raise RuntimeError(msg)
            return [alert] if _mode == "alerts" else []

        monkeypatch.setattr(IrrigationLearner, "detect_issues", fake_detect)
        results[mode] = _decided(_logic(env).decide_for_cluster(cid))

    assert _decision_core(results["raises"]) == _decision_core(results["empty"])
    assert _decision_core(results["alerts"]) == _decision_core(results["empty"])
    assert results["raises"].stress_indicators.learning_alerts == []
    assert results["alerts"].stress_indicators.learning_alerts == [
        {"type": "blocked_drip", "severity": "critical", "message": "drip blocked"}
    ]
    assert TriggerCode.LEARNING_ALERT not in _codes(results["alerts"])


def test_inv4_learner_is_consulted_once_per_sensor_path_evaluation(
    env: IrrigationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant #4: the engine calls the learner exactly once per (non-terminal) evaluation."""
    cid, _, _ = _cluster(env)
    calls: list[int] = []

    def record(_self: IrrigationLearner, cluster_id: int) -> list[Alert]:
        calls.append(cluster_id)
        return []

    monkeypatch.setattr(IrrigationLearner, "detect_issues", record)

    _logic(env).decide_for_cluster(cid)

    assert calls == [cid]


# ── #5 Thresholds live in constants.py ───────────────────────────────────────


@pytest.mark.usefixtures("env")
def test_inv5_engine_constants_mirror_constants_module() -> None:
    """Invariant #5: every UPPER_CASE name the engine imports is the constants.py value."""
    imported = {n for n in vars(engine_mod) if n.isupper() and hasattr(constants_mod, n)}

    assert len(imported) >= MIN_ENGINE_CONSTANTS
    for name in imported:
        assert getattr(engine_mod, name) == getattr(constants_mod, name), name


def test_inv5_thresholds_are_bound_at_import_current_behavior(
    env: IrrigationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant #5: the engine reads thresholds via its *own* module globals.

    Pins current behavior: ``from constants import MIN_COOLDOWN_HOURS`` binds the
    value at import, so patching ``greenhouse_core.constants`` does nothing, while
    patching ``greenhouse_core.logic.engine.MIN_COOLDOWN_HOURS`` moves the decision
    (read at call time). A refactor that switches to ``constants.X`` attribute
    access would flip the first assertion — that is a behavior change for tests.
    """
    cid, irr, _ = _cluster(env)
    env.add_irrigation_event(
        irr, EventAction.START, TriggeredBy.AUTO, duration_minutes=2, timestamp=FROZEN_TS - 3 * HOUR
    )

    monkeypatch.setattr(constants_mod, "MIN_COOLDOWN_HOURS", 2)
    assert _decided(_logic(env).decide_for_cluster(cid)).primary_code is TriggerCode.COOLDOWN

    monkeypatch.setattr(engine_mod, "MIN_COOLDOWN_HOURS", 2)
    decision = _logic(env).decide_for_cluster(cid)
    assert decision is not None
    assert decision.action is Action.IRRIGATE
    assert TriggerCode.COOLDOWN not in _codes(decision)


def test_inv5_inline_literals_and_explicit_no_action_reason(env: IrrigationRepository) -> None:
    """Invariant #5 / #6: forecast horizon, rain threshold and base confidence; no empty reason trail.

    Forecast horizon ``hours=6``, rain threshold ``> 2.0`` mm, base confidence ``0.5``.
    A temperature-only reading inside the ideal range used to leave ``reasons``
    empty (``primary_code`` NULL); it now carries ``NO_ACTION_NEEDED``.
    """
    cid, _, _ = _cluster(env, environment="outdoor")
    weather = FakeForecastWeather({"precipitation_mm": 2.0})

    decision = _logic(env, weather_client=weather).decide_for_cluster(cid)
    assert decision is not None

    assert weather.forecast_calls == [6]
    assert decision.primary_code is TriggerCode.SENSOR_DRY  # 2.0 mm is not "> 2.0"
    weather_rain = FakeForecastWeather({"precipitation_mm": 2.0001})
    assert (
        _decided(_logic(env, weather_client=weather_rain).decide_for_cluster(cid)).primary_code
        is TriggerCode.WEATHER_SKIP
    )

    temp_only_cid = env.add_cluster("Temp only")
    env.add_plant(cluster_id=temp_only_cid, species="Monstera deliciosa", category="tropical")
    sid = env.add_sensor(
        cluster_id=temp_only_cid, tuya_device_id="fake_tuya_sensor_inv9999", name="T", sensor_type="x", config={}
    )
    env.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, temperature=22.0)
    no_rule = _logic(env).decide_for_cluster(temp_only_cid, persist=True)
    assert no_rule is not None
    assert (no_rule.action, no_rule.confidence) == (Action.SKIP, 0.5)
    assert no_rule.primary_code is TriggerCode.NO_ACTION_NEEDED
    assert no_rule.reason_text == "readings within range — no action needed"
    assert _log_rows(env)[-1].primary_code == "no_action_needed"


# ── #6 One DecisionLog per evaluation ────────────────────────────────────────


class _DecideKwargs(TypedDict, total=False):
    """Extra ``decide_for_cluster`` arguments a scenario needs."""

    current_temp: float


_Scenario = tuple[int, IrrigationLogic, _DecideKwargs]


def _log_rows(db: IrrigationRepository) -> list[DecisionLog]:
    return list(db.session.scalars(select(DecisionLog).order_by(DecisionLog.id)))


def _scenario_no_plants(db: IrrigationRepository) -> _Scenario:
    return db.add_cluster("Empty"), _logic(db), {}


def _scenario_leak_hold(db: IrrigationRepository) -> _Scenario:
    cid, _, _ = _cluster(db)
    _raise_leak(db, cid)
    return cid, _logic(db), {}


def _scenario_cooldown(db: IrrigationRepository) -> _Scenario:
    cid, irr, _ = _cluster(db)
    db.add_irrigation_event(irr, EventAction.START, TriggeredBy.AUTO, duration_minutes=2, timestamp=FROZEN_TS - HOUR)
    return cid, _logic(db), {}


def _scenario_quiet_hours(db: IrrigationRepository) -> _Scenario:
    cid, _, _ = _cluster(db)
    db.update_global_irrigation_config(quiet_start_hour=9, quiet_end_hour=11)
    return cid, _logic(db), {}


def _scenario_weather_skip(db: IrrigationRepository) -> _Scenario:
    cid, _, _ = _cluster(db, environment="outdoor")
    return cid, _logic(db, weather_client=FakeForecastWeather({"precipitation_mm": 9.0})), {}


def _scenario_temp_fallback(db: IrrigationRepository) -> _Scenario:
    cid = db.add_cluster("No sensors")
    db.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical")
    return cid, _logic(db), {"current_temp": 30.0}


def _scenario_water_warning(db: IrrigationRepository) -> _Scenario:
    cid, _, _ = _cluster(db, soils=())
    sid = db.add_sensor(cluster_id=cid, tuya_device_id="fake_tuya_sensor_invww", name="WW", sensor_type="x", config={})
    db.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=50.0, water_warning=True)
    return cid, _logic(db), {}


def _scenario_water_stress(db: IrrigationRepository) -> _Scenario:
    cid, _, _ = _cluster(db, soils=(20.0,))
    return cid, _logic(db), {}


def _scenario_outside_window(db: IrrigationRepository) -> _Scenario:
    cid, _, _ = _cluster(db)
    db.add_irrigation_window(cid, start_hour=6, end_hour=8)
    return cid, _logic(db), {}


def _scenario_sensor_dry(db: IrrigationRepository) -> _Scenario:
    cid, _, _ = _cluster(db)
    return cid, _logic(db), {}


def _scenario_sensor_adequate(db: IrrigationRepository) -> _Scenario:
    cid, _, _ = _cluster(db, soils=(55.0,))
    return cid, _logic(db), {}


_SCENARIOS: dict[str, Callable[[IrrigationRepository], _Scenario]] = {
    "no_plants": _scenario_no_plants,
    "leak_hold": _scenario_leak_hold,
    "cooldown": _scenario_cooldown,
    "quiet_hours": _scenario_quiet_hours,
    "weather_skip": _scenario_weather_skip,
    "temp_fallback": _scenario_temp_fallback,
    "water_warning": _scenario_water_warning,
    "water_stress": _scenario_water_stress,
    "outside_window": _scenario_outside_window,
    "sensor_dry": _scenario_sensor_dry,
    "sensor_adequate": _scenario_sensor_adequate,
}


def _scenario(db: IrrigationRepository, name: str) -> _Scenario:
    """Build one cluster per engine exit branch; returns ``(cluster_id, logic, kwargs)``."""
    if name not in _SCENARIOS:
        raise ValueError(name)
    return _SCENARIOS[name](db)


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
def test_inv6_every_exit_branch_persists_exactly_one_decision_log(
    env: IrrigationRepository, name: str, code: TriggerCode, action: Action
) -> None:
    """Invariant #6: every evaluation (acted-on or not) writes one ``decision_logs`` row with persist=True.

    The row mirrors the decision (primary_code, reason_text, action, dosage), is
    ``actuated=False`` (only the service flips it after a real start), and its
    ``payload_json`` round-trips to the same ``IrrigationDecision``.
    """
    cid, logic, kwargs = _scenario(env, name)

    # A trigger outside TriggeredBy on purpose: the log row stores whatever the caller passed.
    decision = logic.decide_for_cluster(cid, persist=True, triggered_by=cast("TriggeredBy", "scheduler"), **kwargs)
    assert decision is not None

    rows = _log_rows(env)
    assert len(rows) == 1
    row = rows[0]
    assert decision.primary_code is code
    assert decision.action is action
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
def test_inv6_persist_false_writes_nothing(
    env: IrrigationRepository, name: str, code: TriggerCode, action: Action
) -> None:
    """Invariant #6 scope: persistence is opt-in (``persist=True``); the default writes no row."""
    del action  # parametrize column kept for the test id; unused here
    cid, logic, kwargs = _scenario(env, name)

    decision = logic.decide_for_cluster(cid, **kwargs)
    assert decision is not None

    assert decision.primary_code is code
    assert decision.decision_log_id is None
    assert _log_rows(env) == []


def test_inv6_unknown_cluster_returns_none_and_writes_nothing(env: IrrigationRepository) -> None:
    """Invariant #6 scope: an unknown cluster is not an evaluation — ``None``, no row."""
    assert _logic(env).decide_for_cluster(424242, persist=True) is None
    assert _log_rows(env) == []


def test_inv6_persistence_failure_never_blocks_the_decision(
    env: IrrigationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant #6: ``_persist`` is best-effort — a failing write still returns the decision."""
    cid, _, _ = _cluster(env)

    def boom(*_args: object, **_kwargs: object) -> Never:
        msg = "db down"
        raise RuntimeError(msg)

    monkeypatch.setattr(env, "add_decision_log", boom)
    decision = _logic(env).decide_for_cluster(cid, persist=True)
    assert decision is not None

    assert decision.action is Action.IRRIGATE
    assert decision.decision_log_id is None


def test_inv6_reasons_stay_a_list_of_reason_through_the_whole_pipeline(env: IrrigationRepository) -> None:
    """Invariant #6: ``reasons`` is ``list[Reason]`` on a decision that ran every mutating rule.

    (Assignment coercion itself is pinned by
    ``test_engine_timing.py::TestDecisionAssignmentValidation``.)
    """
    cid, _, _ = _cluster(env, soils=(38.0,), species="Unknown fern", category="fern")
    env.add_vacation_window(starts_at=FROZEN_TS - HOUR, ends_at=FROZEN_TS + 3 * 86400)

    decision = _logic(env).decide_for_cluster(cid)
    assert decision is not None

    assert IrrigationDecision.model_config["validate_assignment"] is True
    assert type(decision.reasons) is list
    assert all(type(r) is Reason for r in decision.reasons)
    assert _codes(decision) == [TriggerCode.SENSOR_VERY_DRY, TriggerCode.WATER_NEEDS_HIGH, TriggerCode.VACATION_ACTIVE]


# ── #9 Plant-DB timing precedence ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("species", "category", "environment", "at_month", "expected"),
    [
        # species-level indoor multiplier (Alocasia winter 0.4) beats tropical category (0.5)
        ("Alocasia amazonica", "tropical", "indoor", 1, "winter multiplier 0.4\u00d7"),
        # no species timing → tropical _category_defaults (summer 1.2 == built-in, autumn 0.8)
        ("Monstera deliciosa", "tropical", "indoor", 10, "autumn multiplier 0.8\u00d7"),
        # species _outdoor key (Loquat summer 1.5) beats category _outdoor (1.6)
        ("Eriobotrya japonica", "fruit_tree", "outdoor", 7, "summer multiplier 1.5\u00d7"),
        # Loquat has no indoor key → category fruit_tree indoor (summer 1.5)
        ("Eriobotrya japonica", "fruit_tree", "indoor", 7, "summer multiplier 1.5\u00d7"),
        # unknown species → category fruit_tree _outdoor (summer 1.6)
        ("Unknown fruit tree", "fruit_tree", "outdoor", 7, "summer multiplier 1.6\u00d7"),
        # fern has no _outdoor key → falls to built-in outdoor table (winter 0.3), NOT fern indoor (0.7)
        ("Unknown fern", "fern", "outdoor", 1, "winter multiplier 0.3\u00d7"),
        # no species, no category → built-in indoor table (winter 0.5)
        ("Plantus fictitius", None, "indoor", 1, "winter multiplier 0.5\u00d7"),
    ],
)
def test_inv9_seasonal_multiplier_precedence_species_category_constants(  # noqa: PLR0913 — one argument per parametrize column
    tmp_db: IrrigationRepository,
    clean_env: Path,
    *,
    species: str,
    category: str | None,
    environment: str,
    at_month: int,
    expected: str,
) -> None:
    """Invariant #9: species > ``_category_defaults`` > constants; outdoor uses ``*_outdoor`` (no indoor fallback)."""
    del clean_env  # side-effect fixture (a hermetic environment)
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
        assert decision is not None

    seasonal = [r for r in decision.reasons if r.code in (TriggerCode.SEASONAL_HOLD, TriggerCode.SEASONAL_BOOST)]
    assert len(seasonal) == 1
    assert seasonal[0].message.startswith(expected)
    assert seasonal[0].message.endswith(f"for {environment} cluster")


def test_inv9_preferred_water_hours_never_gate_without_window_rows(env: IrrigationRepository) -> None:
    """Invariant #9: no ``IrrigationWindow`` rows → all hours allowed (issue #83).

    Loquat prefers 05-09 local; at 10:00 UTC with no windows it still irrigates,
    and adding a row that excludes 10:00 is what produces OUTSIDE_WINDOW.
    """
    cid, _, _ = _cluster(env, species="Eriobotrya japonica", category="fruit_tree")

    assert _decided(_logic(env).decide_for_cluster(cid)).action is Action.IRRIGATE

    env.add_irrigation_window(cid, start_hour=5, end_hour=9)
    assert _decided(_logic(env).decide_for_cluster(cid)).primary_code is TriggerCode.OUTSIDE_WINDOW


# ── #10 Cleaned view for judgements, raw rows for the archive ────────────────


def test_inv10_spike_is_ignored_by_the_decision_and_raw_rows_are_untouched(env: IrrigationRepository) -> None:
    """Invariant #10: a Hampel-rejected spike does not drive the call; ``sensor_readings`` is never mutated."""
    cid = env.add_cluster("Spike")
    env.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical")
    sid = env.add_sensor(cluster_id=cid, tuya_device_id="fake_tuya_sensor_spk", name="S", sensor_type="x", config={})
    series = [55.0, 55.0, 55.0, 55.0, 10.0, 55.0, 55.0, 55.0, 55.0]
    for i, soil in enumerate(series):
        env.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - (len(series) - 1 - i) * HOUR, soil_moisture=soil)
    before = [(r.id, r.timestamp, r.soil_moisture) for r in env.session.scalars(select(SensorReading))]

    decision = _logic(env).decide_for_cluster(cid, persist=True)
    assert decision is not None

    assert decision.sensor_snapshot is not None
    assert decision.sensor_snapshot.min_soil_moisture == HEALTHY_SOIL
    assert decision.primary_code is TriggerCode.SENSOR_ADEQUATE
    after = [(r.id, r.timestamp, r.soil_moisture) for r in env.session.scalars(select(SensorReading))]
    assert after == before
    assert SPIKE_SOIL in [soil for _, _, soil in after]


def test_inv10_short_series_spike_is_filtered(env: IrrigationRepository) -> None:
    """Invariant #10 edge: a series shorter than the Hampel window is judged against its own median.

    Four readings 55/55/10/55 (≥ ``CLEANING_HAMPEL_MIN_READINGS`` = 3): the lone 10 is
    a spike, so it no longer drives the driest-plant rule or the stress override;
    the raw row stays in ``sensor_readings``.
    """
    cid = env.add_cluster("Short")
    env.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical")
    sid = env.add_sensor(cluster_id=cid, tuya_device_id="fake_tuya_sensor_sht", name="S", sensor_type="x", config={})
    for i, soil in enumerate([55.0, 55.0, 10.0, 55.0]):
        env.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - (3 - i) * HOUR, soil_moisture=soil)

    decision = _logic(env).decide_for_cluster(cid)
    assert decision is not None

    assert decision.sensor_snapshot is not None
    assert decision.sensor_snapshot.min_soil_moisture == HEALTHY_SOIL
    assert decision.stress_indicators.water_stress is None
    assert decision.primary_code is TriggerCode.SENSOR_ADEQUATE
    assert SPIKE_SOIL in [r.soil_moisture for r in env.session.scalars(select(SensorReading))]


# ── #11 A leak alert IS the hold ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("age_s", "held"),
    [(LEAK_HOLD_HOURS * HOUR - 1, True), (LEAK_HOLD_HOURS * HOUR, False)],
    ids=["LEAK_HOLD_HOURS-1s", "exactly-LEAK_HOLD_HOURS"],
)
def test_inv11_hold_window_edge_is_exclusive(env: IrrigationRepository, age_s: int, *, held: bool) -> None:
    """Invariant #11: the hold lasts ``LEAK_HOLD_HOURS`` from ``last_seen_at``, exclusive at the edge.

    (The +60 s expiry is pinned in ``tests/test_leak_hold.py``; acknowledge-still-holds,
    resolve-releases and force-does-not-bypass in the test below.)
    """
    cid, _, _ = _cluster(env)
    _raise_leak(env, cid, age_s=age_s)

    decision = _logic(env).decide_for_cluster(cid)
    assert decision is not None

    if held:
        assert decision.primary_code is TriggerCode.LEAK_HOLD
        assert "(0.0h left)" in decision.reasons[0].message
        assert (decision.duration_minutes, decision.interval_hours) == (0, LEAK_HOLD_HOURS)
    else:
        assert decision.action is Action.IRRIGATE


def test_inv11_hold_is_never_written_as_an_irrigation_event(env: IrrigationRepository) -> None:
    """Invariant #11: the hold lives in the alert inbox only — no ``IrrigationEvent`` is written.

    Also: acknowledging keeps the hold, ``bypass_quiet_hours`` does not bypass it,
    resolving releases it — evaluated in sequence on one cluster.
    """
    cid, _, _ = _cluster(env)
    alert = _raise_leak(env, cid)
    logic = _logic(env)

    assert _decided(logic.decide_for_cluster(cid, persist=True)).primary_code is TriggerCode.LEAK_HOLD
    env.acknowledge_alert(alert.id)
    assert _decided(logic.decide_for_cluster(cid, persist=True)).primary_code is TriggerCode.LEAK_HOLD
    forced = logic.decide_for_cluster(cid, persist=True, bypass_quiet_hours=True, triggered_by=TriggeredBy.MANUAL)
    assert forced is not None
    assert forced.primary_code is TriggerCode.LEAK_HOLD
    env.resolve_alert(alert.id)
    assert _decided(logic.decide_for_cluster(cid, persist=True)).action is Action.IRRIGATE

    assert env.session.scalar(select(func.count()).select_from(IrrigationEvent)) == 0
    assert [r.primary_code for r in _log_rows(env)] == ["leak_hold", "leak_hold", "leak_hold", "sensor_dry"]
