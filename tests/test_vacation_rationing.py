"""Vacation reservoir-rationing rule — engine + repository coverage.

The engine's final adjustment ``_apply_vacation_budget`` clamps the chosen
dosage against a per-irrigator reservoir burn-down envelope while a
:class:`VacationWindow` is active. These tests pin the wall clock so the rule
path is deterministic, drive a *moderately* dry Monstera (38% < target 45% but
above the very-dry/critical thresholds) so the engine reaches a normal IRRIGATE
of ``DEFAULT_DURATION_MINUTES`` (2 min) — the critical-stress override would
otherwise return early and bypass the window/seasonal/vacation steps entirely.

Monstera inherits the tropical category window (07–10 local), so all decisions
are evaluated at 08:00 UTC to stay inside it. Vacation consumption events are
seeded well outside the 6h global cooldown so the cooldown gate does not
pre-empt the rationing path.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.logic.decision import Action, TriggerCode
from greenhouse_core.plant_db import get_plant_database

DAY = 86400


def _ts(year: int, month: int, day: int, hour: int, tz: str = "UTC") -> int:
    return int(datetime(year, month, day, hour, tzinfo=ZoneInfo(tz)).timestamp())


def _freeze(monkeypatch, ts: int) -> None:
    """Pin ``time.time()`` process-wide. Call before seeding sensor data."""
    monkeypatch.setattr("time.time", lambda: ts)


@pytest.fixture
def logic(tmp_db):
    return IrrigationLogic(tmp_db, get_plant_database())


def _make_cluster(db, *, moisture: float = 38.0, device_id: str = "fake_pump_a") -> dict:
    """Indoor Monstera cluster with one (capacity-less) irrigator + dry soil."""
    cluster_id = db.add_cluster("Vacation Cluster", environment="indoor")
    db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
    irrigator_id = db.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id=device_id,
        name="Pump A",
        irrigator_type="tuya_cloud",
        config={},
    )
    sensor_id = db.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="fake_soil_vac",
        name="Soil",
        sensor_type="soil_moisture",
        config={},
    )
    db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=moisture)
    return {"cluster_id": cluster_id, "irrigator_id": irrigator_id, "sensor_id": sensor_id}


def _set_capacity(db, irrigator_id: int, *, reservoir_l: float, flow_rate_l_per_min: float) -> None:
    irr = db.get_irrigator(irrigator_id)
    irr.reservoir_l = reservoir_l
    irr.flow_rate_l_per_min = flow_rate_l_per_min
    db.session.flush()


def _codes(decision):
    return [r.code for r in decision.reasons]


# ── No vacation / no capacity ────────────────────────────────────────────────


def test_no_active_vacation_is_noop(tmp_db, logic, monkeypatch):
    """Without an active vacation the rule does nothing — normal IRRIGATE, no vacation reasons."""
    _freeze(monkeypatch, _ts(2026, 5, 14, 8))
    ctx = _make_cluster(tmp_db)
    _set_capacity(tmp_db, ctx["irrigator_id"], reservoir_l=10.0, flow_rate_l_per_min=1.0)

    decision = logic.decide_for_cluster(ctx["cluster_id"])

    assert decision is not None
    assert decision.action is Action.IRRIGATE
    assert decision.duration_minutes == 2  # DEFAULT_DURATION_MINUTES
    assert TriggerCode.VACATION_ACTIVE not in _codes(decision)


def test_vacation_active_no_capacity_normal_irrigation(tmp_db, logic, monkeypatch):
    """Vacation active but irrigator has no reservoir/flow → normal irrigation + VACATION_ACTIVE present."""
    now = _ts(2026, 5, 14, 8)
    _freeze(monkeypatch, now)
    ctx = _make_cluster(tmp_db)  # no _set_capacity → uncapped
    tmp_db.add_vacation_window(starts_at=now - DAY, ends_at=now + 5 * DAY)

    decision = logic.decide_for_cluster(ctx["cluster_id"])

    assert decision is not None
    assert decision.action is Action.IRRIGATE
    assert decision.duration_minutes == 2  # unchanged
    assert TriggerCode.VACATION_ACTIVE in _codes(decision)
    assert TriggerCode.VACATION_RATIONING not in _codes(decision)


# ── Budget envelope outcomes ─────────────────────────────────────────────────


def test_vacation_plenty_of_budget_unchanged(tmp_db, logic, monkeypatch):
    """Large reservoir → headroom far exceeds the 2-min dose → duration unchanged, VACATION_ACTIVE present."""
    now = _ts(2026, 5, 14, 8)
    _freeze(monkeypatch, now)
    ctx = _make_cluster(tmp_db)
    _set_capacity(tmp_db, ctx["irrigator_id"], reservoir_l=1000.0, flow_rate_l_per_min=1.0)
    tmp_db.add_vacation_window(starts_at=now - DAY, ends_at=now + 5 * DAY)

    decision = logic.decide_for_cluster(ctx["cluster_id"])

    assert decision is not None
    assert decision.action is Action.IRRIGATE
    assert decision.duration_minutes == 2
    assert TriggerCode.VACATION_ACTIVE in _codes(decision)
    assert TriggerCode.VACATION_RATIONING not in _codes(decision)
    assert TriggerCode.VACATION_BUDGET_EXHAUSTED not in _codes(decision)


def test_vacation_partial_budget_trims_duration(tmp_db, logic, monkeypatch):
    """Day-0 cumulative allowance gives ~1.9 min headroom → trimmed to 1 min with VACATION_RATIONING.

    reservoir=10 → usable 9.5; 5-day vacation → daily 1.9 L; day_index 0 →
    allowed_cum = 1.9 L; flow 1.0 L/min → max_run 1.9 → floor = 1 min < 2 (dose).
    """
    starts = _ts(2026, 5, 14, 0)
    now = _ts(2026, 5, 14, 8)  # day_index 0
    _freeze(monkeypatch, now)
    ctx = _make_cluster(tmp_db)
    _set_capacity(tmp_db, ctx["irrigator_id"], reservoir_l=10.0, flow_rate_l_per_min=1.0)
    tmp_db.add_vacation_window(starts_at=starts, ends_at=starts + 5 * DAY)

    decision = logic.decide_for_cluster(ctx["cluster_id"])

    assert decision is not None
    assert decision.action is Action.IRRIGATE
    assert decision.duration_minutes == 1
    assert TriggerCode.VACATION_RATIONING in _codes(decision)
    assert TriggerCode.VACATION_ACTIVE in _codes(decision)


def test_vacation_budget_exhausted_flips_to_skip(tmp_db, logic, monkeypatch):
    """Tiny reservoir → sub-minute headroom → SKIP with VACATION_BUDGET_EXHAUSTED, duration 0.

    reservoir=1 → usable 0.95; 5-day → daily 0.19 L; day_index 0 → allowed 0.19 L;
    flow 1.0 → max_run 0.19 → floor 0 < VACATION_MIN_RUN_MINUTES (1).
    """
    starts = _ts(2026, 5, 14, 0)
    now = _ts(2026, 5, 14, 8)
    _freeze(monkeypatch, now)
    ctx = _make_cluster(tmp_db)
    _set_capacity(tmp_db, ctx["irrigator_id"], reservoir_l=1.0, flow_rate_l_per_min=1.0)
    tmp_db.add_vacation_window(starts_at=starts, ends_at=starts + 5 * DAY)

    decision = logic.decide_for_cluster(ctx["cluster_id"])

    assert decision is not None
    assert decision.action is Action.SKIP
    assert decision.duration_minutes == 0
    assert decision.confidence == 0.9  # CONFIDENCE_COOLDOWN
    assert TriggerCode.VACATION_BUDGET_EXHAUSTED in _codes(decision)
    assert TriggerCode.VACATION_ACTIVE in _codes(decision)


def test_spent_consumption_reduces_headroom(tmp_db, logic, monkeypatch):
    """Prior in-window consumption eats the cumulative allowance and forces a SKIP.

    Same generous-looking tank as the trim case (daily 1.9 L) but a prior 2-min
    start event (2 L spent) on day 0 drives headroom negative → SKIP. The event
    is seeded >6h before ``now`` so the cooldown gate does not pre-empt, and
    ``now`` (09:00) stays inside the tropical 07–10 window.
    """
    starts = _ts(2026, 5, 14, 0)
    now = _ts(2026, 5, 14, 9)  # day_index 0, inside 07–10; 8h after the 01:00 event (> 6h cooldown)
    _freeze(monkeypatch, now)
    ctx = _make_cluster(tmp_db)
    _set_capacity(tmp_db, ctx["irrigator_id"], reservoir_l=10.0, flow_rate_l_per_min=1.0)
    tmp_db.add_vacation_window(starts_at=starts, ends_at=starts + 5 * DAY)
    # 2 minutes * 1 L/min = 2 L spent on day 0 > daily allowance 1.9 L.
    tmp_db.add_irrigation_event(
        irrigator_id=ctx["irrigator_id"],
        action="start",
        triggered_by="auto",
        duration_minutes=2,
        timestamp=_ts(2026, 5, 14, 1),
    )

    decision = logic.decide_for_cluster(ctx["cluster_id"])

    assert decision is not None
    assert decision.action is Action.SKIP
    assert TriggerCode.VACATION_BUDGET_EXHAUSTED in _codes(decision)


def test_tightest_tank_binds_shared_duration(tmp_db, logic, monkeypatch):
    """Two capacity irrigators — the smaller tank constrains the shared duration.

    Pump A: reservoir 1000 (effectively unlimited). Pump B: reservoir 10 →
    day-0 allowance 1.9 L, flow 1.0 → 1 min. The shared duration is trimmed to
    1 min (the tighter of the two), not left at 2.
    """
    starts = _ts(2026, 5, 14, 0)
    now = _ts(2026, 5, 14, 8)
    _freeze(monkeypatch, now)
    ctx = _make_cluster(tmp_db)
    _set_capacity(tmp_db, ctx["irrigator_id"], reservoir_l=1000.0, flow_rate_l_per_min=1.0)
    pump_b = tmp_db.add_irrigator(
        cluster_id=ctx["cluster_id"],
        tuya_device_id="fake_pump_b",
        name="Pump B",
        irrigator_type="tuya_cloud",
        config={},
    )
    _set_capacity(tmp_db, pump_b, reservoir_l=10.0, flow_rate_l_per_min=1.0)
    tmp_db.add_vacation_window(starts_at=starts, ends_at=starts + 5 * DAY)

    decision = logic.decide_for_cluster(ctx["cluster_id"])

    assert decision is not None
    assert decision.action is Action.IRRIGATE
    assert decision.duration_minutes == 1
    assert TriggerCode.VACATION_RATIONING in _codes(decision)


def test_cluster_with_no_irrigators_does_not_crash(tmp_db, logic, monkeypatch):
    """Vacation active, cluster has no irrigators → engine still returns a decision (no crash)."""
    now = _ts(2026, 5, 14, 8)
    _freeze(monkeypatch, now)
    cluster_id = tmp_db.add_cluster("Sensor-only", environment="indoor")
    tmp_db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
    sensor_id = tmp_db.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="fake_soil_only",
        name="Soil",
        sensor_type="soil_moisture",
        config={},
    )
    tmp_db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=38.0)
    tmp_db.add_vacation_window(starts_at=now - DAY, ends_at=now + 5 * DAY)

    decision = logic.decide_for_cluster(cluster_id)

    assert decision is not None
    # No capacity irrigators → VACATION_ACTIVE present, dosage untouched.
    assert TriggerCode.VACATION_ACTIVE in _codes(decision)
    assert decision.action is Action.IRRIGATE
    assert decision.duration_minutes == 2


# ── Repository: irrigator_consumption_liters ─────────────────────────────────


def test_irrigator_consumption_liters_sums_starts_times_flow(tmp_db):
    """Sum of in-window ``start`` duration_minutes × flow_rate gives liters."""
    cluster_id = tmp_db.add_cluster("Consumption", environment="indoor")
    irr = tmp_db.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id="fake_pump_consume",
        name="Pump",
        irrigator_type="tuya_cloud",
        config={},
    )
    _set_capacity(tmp_db, irr, reservoir_l=10.0, flow_rate_l_per_min=2.0)
    # In window: 3 + 2 = 5 minutes of starts → 5 * 2.0 = 10.0 L.
    tmp_db.add_irrigation_event(
        irrigator_id=irr, action="start", triggered_by="auto", duration_minutes=3, timestamp=100
    )
    tmp_db.add_irrigation_event(
        irrigator_id=irr, action="start", triggered_by="auto", duration_minutes=2, timestamp=200
    )
    # Excluded: a stop event, and a start outside the window.
    tmp_db.add_irrigation_event(irrigator_id=irr, action="stop", triggered_by="auto", duration_minutes=9, timestamp=150)
    tmp_db.add_irrigation_event(
        irrigator_id=irr, action="start", triggered_by="auto", duration_minutes=9, timestamp=999
    )

    liters = tmp_db.irrigator_consumption_liters(irr, since=0, until=300)

    assert liters == pytest.approx(10.0)


def test_irrigator_consumption_liters_none_flow_returns_zero(tmp_db):
    """No configured flow rate → 0.0 regardless of recorded start events."""
    cluster_id = tmp_db.add_cluster("NoFlow", environment="indoor")
    irr = tmp_db.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id="fake_pump_noflow",
        name="Pump",
        irrigator_type="tuya_cloud",
        config={},
    )
    tmp_db.add_irrigation_event(irrigator_id=irr, action="start", triggered_by="auto", duration_minutes=5, timestamp=50)

    assert tmp_db.irrigator_consumption_liters(irr, since=0, until=100) == 0.0


def test_irrigator_consumption_liters_unknown_irrigator_returns_zero(tmp_db):
    """Unknown irrigator id → 0.0 (no crash)."""
    assert tmp_db.irrigator_consumption_liters(9999, since=0, until=100) == 0.0
