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
