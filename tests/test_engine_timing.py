"""Integration tests — plant DB timing fields drive the engine end-to-end.

Covers the fallback chain that joins ``plant_database.json`` to
:class:`greenhouse_core.logic.IrrigationLogic`:

* With no SQL windows, ``_apply_window_rule`` is a no-op — watering is allowed at
  any hour (issue #83); ``preferred_water_hours_local`` is advisory only.
* ``season_frequency_multiplier{,_outdoor}`` (species > category default >
  built-in) is consulted by
  ``_apply_seasonal_multiplier`` and the engine picks the ``_outdoor`` variant
  when ``cluster.environment == "outdoor"``.

Time is pinned via ``monkeypatch`` so the rule path is deterministic regardless
of the calendar day the suite runs. ``_freeze`` patches ``time.time``
process-wide so the engine's ``evaluated_at`` and the repository's reading
cutoff share the same wall clock; seed sensor data *after* freezing.
"""

from __future__ import annotations

import time
import warnings
from datetime import datetime
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

import pydantic
import pytest

from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.logic.decision import Action, IrrigationDecision, Reason, TriggerCode
from greenhouse_core.models import EventAction, TriggeredBy
from greenhouse_core.plant_db import get_plant_database

if TYPE_CHECKING:
    from greenhouse_core.repository import IrrigationRepository


def _ts(year: int, month: int, day: int, hour: int, tz: str = "UTC") -> int:
    return int(datetime(year, month, day, hour, tzinfo=ZoneInfo(tz)).timestamp())


def _freeze(monkeypatch: pytest.MonkeyPatch, ts: int) -> None:
    """Pin ``time.time()`` process-wide. Call before seeding sensor data."""
    monkeypatch.setattr("time.time", lambda: ts)


def _add_soil(db: IrrigationRepository, cluster_id: int, moisture: float, *, device_id: str = "fake_soil_a") -> int:
    sid = db.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id=device_id,
        name="Fake Soil",
        sensor_type="tuya.tr301z",
        config={},
    )
    db.add_sensor_reading(sensor_id=sid, soil_moisture=moisture)
    return sid


@pytest.fixture
def logic(tmp_db: IrrigationRepository) -> IrrigationLogic:
    """Provide an engine on the test database and the bundled plant database."""
    return IrrigationLogic(tmp_db, get_plant_database())


class TestNoWindowsAllowAllHours:
    """With no IrrigationWindow rows, the engine allows watering at any hour.

    ``preferred_water_hours_local`` is advisory plant data only — it no longer
    gates actuation (issue #83). Night protection is delegated to quiet hours.
    """

    @pytest.mark.parametrize("hour", [8, 14])
    def test_alocasia_no_windows_does_not_skip(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch, hour: int
    ) -> None:
        """Alocasia with no windows must NOT skip with OUTSIDE_WINDOW, at 08:00 or 14:00.

        Soil is held *moderately* dry (35%) — below the adequate band but above
        ``SOIL_MOISTURE_CRITICAL`` so the critical-stress override does not
        short-circuit; the decision still flows through the (now no-op) window
        rule rather than being forced by stress.
        """
        _freeze(monkeypatch, _ts(2026, 5, 14, hour))
        cluster_id = tmp_db.add_cluster("Indoor Tropical")
        tmp_db.add_plant(cluster_id=cluster_id, species="Alocasia amazonica", category="tropical")
        _add_soil(tmp_db, cluster_id, moisture=35.0)

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        codes = [r.code for r in decision.reasons]
        assert TriggerCode.OUTSIDE_WINDOW not in codes

    def test_unknown_species_no_windows_does_not_skip(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No species + no category + no windows → no OUTSIDE_WINDOW gate at any hour."""
        _freeze(monkeypatch, _ts(2026, 5, 14, 5))
        cluster_id = tmp_db.add_cluster("Mystery Plant Cluster")
        tmp_db.add_plant(
            cluster_id=cluster_id,
            species="Whateverium fake",
            category=None,
            water_needs="medium",
        )
        _add_soil(tmp_db, cluster_id, moisture=35.0)

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        codes = [r.code for r in decision.reasons]
        assert TriggerCode.OUTSIDE_WINDOW not in codes

    def test_dry_soil_no_windows_midday_no_quiet_hours_irrigates(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry soil + no windows + 13:00 local + no quiet hours → irrigate, no OUTSIDE_WINDOW.

        Directly exercises the #83 fix: previously the preferred-hours fallback
        silently skipped ~21h/day. Quiet hours are explicitly disabled (0..0) so
        only the window rule could have blocked.
        """
        _freeze(monkeypatch, _ts(2026, 5, 14, 13))
        cluster_id = tmp_db.add_cluster("Midday Dry", environment="indoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
        # Disable quiet hours so the only possible gate is the window rule.
        tmp_db.update_global_irrigation_config(quiet_start_hour=0, quiet_end_hour=0)
        _add_soil(tmp_db, cluster_id, moisture=20.0)  # well below target → dry

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        assert decision.action.value == "irrigate"
        codes = [r.code for r in decision.reasons]
        assert TriggerCode.OUTSIDE_WINDOW not in codes


class TestSeasonalMultiplier:
    """``season_frequency_multiplier{,_outdoor}`` is consumed by the engine."""

    def test_outdoor_fruit_tree_summer_emits_seasonal_boost(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Outdoor loquat in July → species-level outdoor 1.5x summer → SEASONAL_BOOST emitted."""
        # Loquat has preferred_water_hours_local=[5,9]; pick 07:00 to stay inside.
        _freeze(monkeypatch, _ts(2026, 7, 15, 7))
        cluster_id = tmp_db.add_cluster("Garden Loquat", environment="outdoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Eriobotrya japonica", category="fruit_tree")
        _add_soil(tmp_db, cluster_id, moisture=55.0)

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        codes = [r.code for r in decision.reasons]
        assert TriggerCode.SEASONAL_BOOST in codes

    def test_unknown_species_unknown_category_uses_builtin_indoor(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No species + no category → builtin DEFAULT_SEASON_MULTIPLIER_INDOOR → winter 0.5x → SEASONAL_HOLD."""
        # 08:00 in January — inside the global default 06-10 window so the
        # window rule does not pre-empt; the engine reaches the seasonal step.
        _freeze(monkeypatch, _ts(2026, 1, 15, 8))
        cluster_id = tmp_db.add_cluster("Default Indoor", environment="indoor")
        tmp_db.add_plant(
            cluster_id=cluster_id,
            species="Whateverium fake",
            category=None,
            water_needs="medium",
        )
        _add_soil(tmp_db, cluster_id, moisture=55.0)

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        codes = [r.code for r in decision.reasons]
        assert TriggerCode.SEASONAL_HOLD in codes

    def test_winter_multiplier_lengthens_interval(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Indoor tropical: winter (0.5x frequency) → interval STRICTLY GREATER than spring baseline.

        Multiplier semantics are *frequency x baseline* — water 0.5x as often
        means double the interval. This guards the inversion bug that previously
        had winter shortening the interval.
        """
        cluster_id = tmp_db.add_cluster("Indoor Tropical Seasonal", environment="indoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
        sensor_id = tmp_db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id="fake_soil_winter",
            name="Fake Soil",
            sensor_type="tuya.tr301z",
            config={},
        )

        # Spring baseline (multiplier == 1.0 for tropical category) at 08:00,
        # inside the tropical 07-10 window.
        _freeze(monkeypatch, _ts(2026, 4, 15, 8))
        tmp_db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=55.0)
        spring_decision = logic.decide_for_cluster(cluster_id)
        assert spring_decision is not None
        baseline_interval = spring_decision.interval_hours

        # Same cluster, same sensors, January (winter 0.5x frequency → double interval).
        _freeze(monkeypatch, _ts(2026, 1, 15, 8))
        tmp_db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=55.0)
        winter_decision = logic.decide_for_cluster(cluster_id)
        assert winter_decision is not None
        codes = [r.code for r in winter_decision.reasons]
        assert TriggerCode.SEASONAL_HOLD in codes
        assert winter_decision.interval_hours > baseline_interval, (
            f"winter interval {winter_decision.interval_hours}h must be GREATER than "
            f"spring baseline {baseline_interval}h — multiplier 0.5 = water HALF as often = "
            f"interval should DOUBLE (got {winter_decision.interval_hours / baseline_interval:.2f}\u00d7)"
        )

    def test_seasonal_reason_keeps_reasons_a_list(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Indoor Monstera in January → tropical winter 0.5x → SEASONAL_HOLD, appended to a list.

        Regression: the seasonal rule must append to ``reasons``, not rebind it to a tuple.

        A tuple in the ``list[Reason]`` field made every ``model_dump`` emit
        ``PydanticSerializationUnexpectedValue`` (status endpoint, decision_logs
        persist) and broke any later ``add_reason`` with ``AttributeError``.
        """
        _freeze(monkeypatch, _ts(2026, 1, 15, 8))
        cluster_id = tmp_db.add_cluster("Indoor Monstera Room", environment="indoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
        _add_soil(tmp_db, cluster_id, moisture=55.0)

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        assert TriggerCode.SEASONAL_HOLD in [r.code for r in decision.reasons]
        assert isinstance(decision.reasons, list)
        with warnings.catch_warnings():
            warnings.filterwarnings("error", message="Pydantic serializer warnings")
            payload = decision.model_dump(mode="json")
            decision.model_dump()
        assert [r["code"] for r in payload["reasons"]] == [r.code.value for r in decision.reasons]
        decision.add_reason(code=TriggerCode.VACATION_ACTIVE, message="later rule")  # must not raise

    def test_seasonal_multiplier_with_quiet_hours_bypass(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: off-season + forced run during quiet hours must not crash ``_finalize``.

        ``_finalize`` appends ``MANUAL_OVERRIDE_QUIET_HOURS`` after the seasonal
        rule ran; with ``reasons`` rebound to a tuple that raised AttributeError.
        """
        _freeze(monkeypatch, _ts(2026, 1, 15, 2))
        cluster_id = tmp_db.add_cluster("Indoor Winter Override", environment="indoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
        _add_soil(tmp_db, cluster_id, moisture=55.0)
        tmp_db.update_global_irrigation_config(quiet_start_hour=0, quiet_end_hour=5)

        decision = logic.decide_for_cluster(cluster_id, bypass_quiet_hours=True, persist=True)

        assert decision is not None
        codes = [r.code for r in decision.reasons]
        assert TriggerCode.SEASONAL_HOLD in codes
        assert TriggerCode.MANUAL_OVERRIDE_QUIET_HOURS in codes
        assert decision.decision_log_id is not None

    def test_quiet_hours_skip_at_night(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cluster inside the global quiet window must SKIP with QUIET_HOURS code."""
        _freeze(monkeypatch, _ts(2026, 5, 14, 2))  # 02:00 UTC, inside 0..5
        cluster_id = tmp_db.add_cluster("Indoor Late Night", environment="indoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
        _add_soil(tmp_db, cluster_id, moisture=30.0)  # very dry — would otherwise irrigate
        # Seed the global quiet window 0..5 (the migration would normally do this).
        tmp_db.update_global_irrigation_config(quiet_start_hour=0, quiet_end_hour=5)

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        assert decision.action.value == "skip"
        assert decision.primary_code == TriggerCode.QUIET_HOURS

    def test_quiet_hours_cluster_override_disables(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``start == end`` at the cluster level disables an inherited global window."""
        _freeze(monkeypatch, _ts(2026, 5, 14, 2))
        cluster_id = tmp_db.add_cluster("Outdoor Night Run", environment="outdoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Eriobotrya japonica", category="fruit_tree")
        _add_soil(tmp_db, cluster_id, moisture=30.0)
        # Global says quiet 0..5; cluster opts out with 0..0 (disabled).
        tmp_db.update_global_irrigation_config(quiet_start_hour=0, quiet_end_hour=5)
        tmp_db.set_irrigation_config(cluster_id=cluster_id, quiet_start_hour=0, quiet_end_hour=0)

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        codes = [r.code for r in decision.reasons]
        assert TriggerCode.QUIET_HOURS not in codes

    def test_quiet_hours_bypass_attaches_override_warning(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``bypass_quiet_hours=True`` skips the SKIP but logs the override reason."""
        _freeze(monkeypatch, _ts(2026, 5, 14, 2))
        cluster_id = tmp_db.add_cluster("Indoor Manual Override", environment="indoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
        _add_soil(tmp_db, cluster_id, moisture=55.0)  # adequate
        tmp_db.update_global_irrigation_config(quiet_start_hour=0, quiet_end_hour=5)

        decision = logic.decide_for_cluster(cluster_id, bypass_quiet_hours=True)

        assert decision is not None
        assert decision.primary_code != TriggerCode.QUIET_HOURS
        codes = [r.code for r in decision.reasons]
        assert TriggerCode.MANUAL_OVERRIDE_QUIET_HOURS in codes

    def test_cooldown_beats_quiet_hours(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cooldown is the earlier gate — it must take precedence over quiet hours."""
        _freeze(monkeypatch, _ts(2026, 5, 14, 2))
        cluster_id = tmp_db.add_cluster("Indoor Cooldown First", environment="indoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
        _add_soil(tmp_db, cluster_id, moisture=30.0)
        tmp_db.update_global_irrigation_config(quiet_start_hour=0, quiet_end_hour=5)
        # Recent irrigation event within cooldown window.
        irrigator_id = tmp_db.add_irrigator(
            cluster_id=cluster_id, tuya_device_id="fake_pump", name="Pump", irrigator_type="rainpoint.ik10pw", config={}
        )
        tmp_db.add_irrigation_event(
            irrigator_id=irrigator_id,
            action=EventAction.START,
            triggered_by=TriggeredBy.AUTO,
            duration_minutes=3,
            timestamp=int(time.time()) - 3600,
        )

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        assert decision.primary_code == TriggerCode.COOLDOWN

    def test_summer_multiplier_shortens_interval(
        self, tmp_db: IrrigationRepository, logic: IrrigationLogic, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Outdoor fruit tree: summer (1.5x frequency) → interval STRICTLY LESS than spring baseline.

        Multiplier semantics are *frequency x baseline* — water 1.5x as often
        means 2/3 the interval. Inverse of the winter case.
        """
        cluster_id = tmp_db.add_cluster("Garden Loquat Seasonal", environment="outdoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Eriobotrya japonica", category="fruit_tree")
        sensor_id = tmp_db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id="fake_soil_summer",
            name="Fake Soil",
            sensor_type="tuya.tr301z",
            config={},
        )

        # Spring baseline (outdoor 1.0x for fruit_tree). Loquat preferred 05-09;
        # 07:00 stays inside.
        _freeze(monkeypatch, _ts(2026, 4, 15, 7))
        tmp_db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=55.0)
        spring_decision = logic.decide_for_cluster(cluster_id)
        assert spring_decision is not None
        baseline_interval = spring_decision.interval_hours

        # July — loquat outdoor summer multiplier 1.6x → interval shrinks.
        _freeze(monkeypatch, _ts(2026, 7, 15, 7))
        tmp_db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=55.0)
        summer_decision = logic.decide_for_cluster(cluster_id)
        assert summer_decision is not None
        codes = [r.code for r in summer_decision.reasons]
        assert TriggerCode.SEASONAL_BOOST in codes
        assert summer_decision.interval_hours < baseline_interval, (
            f"summer interval {summer_decision.interval_hours}h must be LESS than "
            f"spring baseline {baseline_interval}h — multiplier >1.0 = water MORE often = "
            f"interval should SHRINK"
        )


class TestDecisionAssignmentValidation:
    """IrrigationDecision re-validates assignments (validate_assignment=True)."""

    def _decision(self) -> IrrigationDecision:
        return IrrigationDecision(
            cluster_id=1, evaluated_at=0, action=Action.SKIP, duration_minutes=0, interval_hours=12, confidence=0.5
        )

    def test_tuple_reasons_are_coerced_to_list(self) -> None:
        """Assigning a tuple of reasons is coerced to a list by validation."""
        decision = self._decision()
        # A tuple on purpose: validate_assignment must coerce it to the declared list.
        decision.reasons = cast("list[Reason]", (Reason(code=TriggerCode.SEASONAL_HOLD, message="x"),))
        assert isinstance(decision.reasons, list)
        decision.add_reason(code=TriggerCode.SEASONAL_BOOST, message="y")  # would AttributeError on a tuple
        assert [r.code for r in decision.reasons] == [TriggerCode.SEASONAL_HOLD, TriggerCode.SEASONAL_BOOST]

    def test_wrong_type_fails_at_the_assignment(self) -> None:
        """Assigning a wrongly typed value fails at the assignment, not later."""
        decision = self._decision()
        with pytest.raises(pydantic.ValidationError):
            decision.duration_minutes = cast("int", "a lot")  # wrong type on purpose: validation must refuse it
