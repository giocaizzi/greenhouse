"""Integration tests — plant DB timing fields drive the engine end-to-end.

Covers the fallback chain that joins ``plant_database.json`` to
:class:`greenhouse_core.logic.IrrigationLogic`:

* ``preferred_water_hours_local`` (species > category default > built-in) is
  consulted by ``_apply_window_rule`` when a cluster has no SQL windows.
* ``season_frequency_multiplier{,_outdoor}`` (same precedence) is consulted by
  ``_apply_seasonal_multiplier`` and the engine picks the ``_outdoor`` variant
  when ``cluster.environment == "outdoor"``.

Time is pinned via ``monkeypatch`` so the rule path is deterministic regardless
of the calendar day the suite runs. ``_freeze`` patches ``time.time``
process-wide so the engine's ``evaluated_at`` and the repository's reading
cutoff share the same wall clock; seed sensor data *after* freezing.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.logic.decision import TriggerCode
from greenhouse_core.plant_db import get_plant_database


def _ts(year: int, month: int, day: int, hour: int, tz: str = "UTC") -> int:
    return int(datetime(year, month, day, hour, tzinfo=ZoneInfo(tz)).timestamp())


def _freeze(monkeypatch, ts: int) -> None:
    """Pin ``time.time()`` process-wide. Call before seeding sensor data."""
    monkeypatch.setattr("time.time", lambda: ts)


def _add_soil(db, cluster_id: int, moisture: float, *, device_id: str = "fake_soil_a") -> int:
    sid = db.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id=device_id,
        name="Fake Soil",
        sensor_type="soil_moisture",
        config={},
    )
    db.add_sensor_reading(sensor_id=sid, soil_moisture=moisture)
    return sid


@pytest.fixture
def logic(tmp_db):
    return IrrigationLogic(tmp_db, get_plant_database())


class TestPreferredHoursFallback:
    """``preferred_water_hours_local`` resolves species → category-default → builtin."""

    def test_alocasia_outside_category_window_skips(self, tmp_db, logic, monkeypatch):
        """Alocasia inherits the tropical category window (07–10); 14:00 must skip.

        Soil is held *moderately* dry (35%) — below the adequate band but above
        ``SOIL_MOISTURE_CRITICAL`` so the critical-stress override does not
        bypass the window rule on the way down.
        """
        _freeze(monkeypatch, _ts(2026, 5, 14, 14))
        cluster_id = tmp_db.add_cluster("Indoor Tropical")
        tmp_db.add_plant(cluster_id=cluster_id, species="Alocasia amazonica", category="tropical")
        _add_soil(tmp_db, cluster_id, moisture=35.0)

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        assert decision.action.value == "skip"
        assert decision.primary_code == TriggerCode.OUTSIDE_WINDOW

    def test_alocasia_inside_category_window_no_window_skip(self, tmp_db, logic, monkeypatch):
        """At 08:00 (inside tropical 07–10), OUTSIDE_WINDOW must not appear."""
        _freeze(monkeypatch, _ts(2026, 5, 14, 8))
        cluster_id = tmp_db.add_cluster("Indoor Tropical")
        tmp_db.add_plant(cluster_id=cluster_id, species="Alocasia amazonica", category="tropical")
        _add_soil(tmp_db, cluster_id, moisture=35.0)

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        codes = [r.code for r in decision.reasons]
        assert TriggerCode.OUTSIDE_WINDOW not in codes

    def test_unknown_species_falls_through_to_global_default(self, tmp_db, logic, monkeypatch):
        """No species + no category resolves preferred=None → global default 06–10 applies."""
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
        assert decision.action.value == "skip"
        assert decision.primary_code == TriggerCode.OUTSIDE_WINDOW


class TestSeasonalMultiplier:
    """``season_frequency_multiplier{,_outdoor}`` is consumed by the engine."""

    def test_indoor_tropical_winter_emits_seasonal_hold(self, tmp_db, logic, monkeypatch):
        """Indoor Monstera in January → tropical winter multiplier 0.5× → SEASONAL_HOLD emitted."""
        # 08:00 is inside tropical preferred window (07–10) so the window rule
        # does not pre-empt the seasonal multiplier path.
        _freeze(monkeypatch, _ts(2026, 1, 15, 8))
        cluster_id = tmp_db.add_cluster("Indoor Monstera Room", environment="indoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
        _add_soil(tmp_db, cluster_id, moisture=55.0)  # adequate → no stress path

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        codes = [r.code for r in decision.reasons]
        assert TriggerCode.SEASONAL_HOLD in codes

    def test_outdoor_fruit_tree_summer_emits_seasonal_boost(self, tmp_db, logic, monkeypatch):
        """Outdoor loquat in July → species-level outdoor 1.5× summer → SEASONAL_BOOST emitted."""
        # Loquat has preferred_water_hours_local=[5,9]; pick 07:00 to stay inside.
        _freeze(monkeypatch, _ts(2026, 7, 15, 7))
        cluster_id = tmp_db.add_cluster("Garden Loquat", environment="outdoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Eriobotrya japonica", category="fruit_tree")
        _add_soil(tmp_db, cluster_id, moisture=55.0)

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        codes = [r.code for r in decision.reasons]
        assert TriggerCode.SEASONAL_BOOST in codes

    def test_unknown_species_unknown_category_uses_builtin_indoor(self, tmp_db, logic, monkeypatch):
        """No species + no category → builtin DEFAULT_SEASON_MULTIPLIER_INDOOR → winter 0.5× → SEASONAL_HOLD."""
        # 08:00 in January — inside the global default 06–10 window so the
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

    def test_winter_multiplier_lengthens_interval(self, tmp_db, logic, monkeypatch):
        """Indoor tropical: winter (0.5× frequency) → interval STRICTLY GREATER than spring baseline.

        Multiplier semantics are *frequency × baseline* — water 0.5× as often
        means double the interval. This guards the inversion bug that previously
        had winter shortening the interval.
        """
        cluster_id = tmp_db.add_cluster("Indoor Tropical Seasonal", environment="indoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
        sensor_id = tmp_db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id="fake_soil_winter",
            name="Fake Soil",
            sensor_type="soil_moisture",
            config={},
        )

        # Spring baseline (multiplier == 1.0 for tropical category) at 08:00,
        # inside the tropical 07–10 window.
        _freeze(monkeypatch, _ts(2026, 4, 15, 8))
        tmp_db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=55.0)
        spring_decision = logic.decide_for_cluster(cluster_id)
        assert spring_decision is not None
        baseline_interval = spring_decision.interval_hours

        # Same cluster, same sensors, January (winter 0.5× frequency → double interval).
        _freeze(monkeypatch, _ts(2026, 1, 15, 8))
        tmp_db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=55.0)
        winter_decision = logic.decide_for_cluster(cluster_id)
        assert winter_decision is not None
        codes = [r.code for r in winter_decision.reasons]
        assert TriggerCode.SEASONAL_HOLD in codes
        assert winter_decision.interval_hours > baseline_interval, (
            f"winter interval {winter_decision.interval_hours}h must be GREATER than "
            f"spring baseline {baseline_interval}h — multiplier 0.5 = water HALF as often = "
            f"interval should DOUBLE (got {winter_decision.interval_hours / baseline_interval:.2f}×)"
        )

    def test_quiet_hours_skip_at_night(self, tmp_db, logic, monkeypatch):
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

    def test_quiet_hours_cluster_override_disables(self, tmp_db, logic, monkeypatch):
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

    def test_quiet_hours_bypass_attaches_override_warning(self, tmp_db, logic, monkeypatch):
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

    def test_cooldown_beats_quiet_hours(self, tmp_db, logic, monkeypatch):
        """Cooldown is the earlier gate — it must take precedence over quiet hours."""
        import time as _time

        _freeze(monkeypatch, _ts(2026, 5, 14, 2))
        cluster_id = tmp_db.add_cluster("Indoor Cooldown First", environment="indoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Monstera deliciosa", category="tropical")
        _add_soil(tmp_db, cluster_id, moisture=30.0)
        tmp_db.update_global_irrigation_config(quiet_start_hour=0, quiet_end_hour=5)
        # Recent irrigation event within cooldown window.
        irrigator_id = tmp_db.add_irrigator(
            cluster_id=cluster_id, tuya_device_id="fake_pump", name="Pump", irrigator_type="tuya_cloud", config={}
        )
        tmp_db.add_irrigation_event(
            irrigator_id=irrigator_id,
            action="start",
            triggered_by="auto",
            duration_minutes=3,
            timestamp=int(_time.time()) - 3600,
        )

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        assert decision.primary_code == TriggerCode.COOLDOWN

    def test_summer_multiplier_shortens_interval(self, tmp_db, logic, monkeypatch):
        """Outdoor fruit tree: summer (1.5× frequency) → interval STRICTLY LESS than spring baseline.

        Multiplier semantics are *frequency × baseline* — water 1.5× as often
        means 2/3 the interval. Inverse of the winter case.
        """
        cluster_id = tmp_db.add_cluster("Garden Loquat Seasonal", environment="outdoor")
        tmp_db.add_plant(cluster_id=cluster_id, species="Eriobotrya japonica", category="fruit_tree")
        sensor_id = tmp_db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id="fake_soil_summer",
            name="Fake Soil",
            sensor_type="soil_moisture",
            config={},
        )

        # Spring baseline (outdoor 1.0× for fruit_tree). Loquat preferred 05–09;
        # 07:00 stays inside.
        _freeze(monkeypatch, _ts(2026, 4, 15, 7))
        tmp_db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=55.0)
        spring_decision = logic.decide_for_cluster(cluster_id)
        assert spring_decision is not None
        baseline_interval = spring_decision.interval_hours

        # July — loquat outdoor summer multiplier 1.6× → interval shrinks.
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
