"""Tests for the leak / stuck-valve hold gate in the decision engine.

The detector raises a critical ``leak_or_stuck_valve`` alert; that alert *is*
the hold. ``IrrigationLogic._enforce_leak_hold`` reads the inbox and skips
automatic irrigation while such an alert is unresolved and younger than
``LEAK_HOLD_HOURS``.

Before issue #103 the "24h auto-cancel" was written as a ``schedule_updated``
irrigation event, which ``_enforce_cooldown`` never counted — the hold existed
only in the note text. These tests pin the real behaviour: what holds, what
lifts it, and what it outranks.
"""

import time

import pytest

from fake_data import FAKE_PLANT_SPECIES
from greenhouse_core.constants import LEAK_ALERT_CODE, LEAK_HOLD_HOURS
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.logic.decision import Action, Severity, TriggerCode
from greenhouse_core.plant_db import get_plant_database


class _Fixture:
    """A dry cluster that would otherwise irrigate, plus alert helpers."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_db):
        self.db = tmp_db
        self.logic = IrrigationLogic(self.db, get_plant_database())
        self.cluster_id = self.db.add_cluster("Leak Hold Cluster")
        self.db.add_plant(cluster_id=self.cluster_id, species=FAKE_PLANT_SPECIES, water_needs="medium")
        self.irrigator_id = self.db.add_irrigator(
            cluster_id=self.cluster_id,
            tuya_device_id="fake_irr_hold",
            name="Irrigator",
            irrigator_type="tuya_cloud",
            config={},
        )
        self.sensor_id = self.db.add_sensor(
            cluster_id=self.cluster_id,
            tuya_device_id="fake_sensor_hold",
            name="Dry Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        # Bone dry: without a hold this cluster irrigates.
        self.db.add_sensor_reading(sensor_id=self.sensor_id, soil_moisture=20.0)

    def _raise_leak(self, *, cluster_id=None, seen_at=None, status="open"):
        alert = self.db.upsert_alert(
            dedup_key=f"leak::{LEAK_ALERT_CODE}::{cluster_id or self.cluster_id}::sensor{self.sensor_id}",
            source="leak",
            code=LEAK_ALERT_CODE,
            title="Possible leak or stuck valve detected",
            message="Dry Sensor: soil moisture still rising after irrigation (latest=78.0%)",
            severity="critical",
            entity_type="sensor",
            entity_id=self.sensor_id,
            cluster_id=cluster_id or self.cluster_id,
            seen_at=seen_at,
        )
        if status != "open":
            alert.status = status
            if status == "resolved":
                alert.resolved_at = int(time.time())
        self.db.session.flush()
        return alert


class TestHoldBlocks(_Fixture):
    def test_dry_cluster_irrigates_without_a_hold(self):
        """Baseline — the fixture is only meaningful if it irrigates untouched."""
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.action == Action.IRRIGATE

    def test_open_leak_alert_holds_the_cluster(self):
        self._raise_leak()

        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.action == Action.SKIP
        assert decision.primary_code == TriggerCode.LEAK_HOLD
        assert decision.duration_minutes == 0

    def test_hold_reason_is_critical_and_names_the_alert(self):
        alert = self._raise_leak()

        decision = self.logic.decide_for_cluster(self.cluster_id)
        reason = decision.reasons[0]

        assert reason.severity == Severity.CRITICAL
        assert f"#{alert.id}" in reason.message
        assert "still rising" in reason.message

    def test_acknowledged_alert_still_holds(self):
        """Acknowledging says "seen", not "fixed" — only resolving lifts it."""
        self._raise_leak(status="acknowledged")

        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.primary_code == TriggerCode.LEAK_HOLD

    def test_force_does_not_bypass_the_hold(self):
        """A stuck valve is hardware: the escape hatch is the irrigator route."""
        self._raise_leak()

        decision = self.logic.decide_for_cluster(self.cluster_id, bypass_quiet_hours=True)

        assert decision.action == Action.SKIP
        assert decision.primary_code == TriggerCode.LEAK_HOLD

    def test_hold_outranks_cooldown(self):
        """Both active → report the safety gate, not the routine one."""
        self._raise_leak()
        self.db.add_irrigation_event(
            irrigator_id=self.irrigator_id,
            action="start",
            triggered_by="auto",
            duration_minutes=2,
            timestamp=int(time.time()) - 3600,
        )

        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.primary_code == TriggerCode.LEAK_HOLD

    def test_hold_is_persisted_to_the_decision_log(self):
        self._raise_leak()

        self.logic.decide_for_cluster(self.cluster_id, persist=True)

        logs = self.db.list_decision_logs(cluster_id=self.cluster_id)
        assert logs[0].primary_code == TriggerCode.LEAK_HOLD.value
        assert logs[0].actuated is False


class TestHoldLifts(_Fixture):
    def test_resolved_alert_releases_the_hold(self):
        self._raise_leak(status="resolved")

        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.action == Action.IRRIGATE

    def test_hold_expires_after_the_configured_window(self):
        self._raise_leak(seen_at=int(time.time()) - (LEAK_HOLD_HOURS * 3600 + 60))

        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.action == Action.IRRIGATE

    def test_hold_still_active_just_inside_the_window(self):
        self._raise_leak(seen_at=int(time.time()) - (LEAK_HOLD_HOURS * 3600 - 600))

        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.primary_code == TriggerCode.LEAK_HOLD

    def test_alert_on_another_cluster_does_not_hold_this_one(self):
        other = self.db.add_cluster("Other Cluster")
        self._raise_leak(cluster_id=other)

        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.action == Action.IRRIGATE


class TestGetActiveAlert(_Fixture):
    """Repository helper the gate is built on."""

    def test_returns_none_when_no_alert_exists(self):
        assert self.db.get_active_alert(LEAK_ALERT_CODE, cluster_id=self.cluster_id) is None

    def test_ignores_resolved_alerts(self):
        self._raise_leak(status="resolved")

        assert self.db.get_active_alert(LEAK_ALERT_CODE, cluster_id=self.cluster_id) is None

    def test_since_bounds_how_stale_an_alert_may_be(self):
        now = int(time.time())
        self._raise_leak(seen_at=now - 7200)

        assert self.db.get_active_alert(LEAK_ALERT_CODE, cluster_id=self.cluster_id, since=now - 3600) is None
        assert self.db.get_active_alert(LEAK_ALERT_CODE, cluster_id=self.cluster_id, since=now - 10800) is not None

    def test_ignores_other_codes(self):
        self.db.upsert_alert(
            dedup_key="anomaly::sensor_drift::1::sensor1",
            source="anomaly",
            code="sensor_drift",
            title="Drift",
            message="drifting",
            severity="warning",
            cluster_id=self.cluster_id,
        )

        assert self.db.get_active_alert(LEAK_ALERT_CODE, cluster_id=self.cluster_id) is None
