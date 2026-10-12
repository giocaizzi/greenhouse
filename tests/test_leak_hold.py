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
from greenhouse_core.models import ActivitySource, Alert, EntityType, EventAction, TriggeredBy
from greenhouse_core.plant_db import get_plant_database
from greenhouse_core.repository import IrrigationRepository


class _Fixture:
    """A dry cluster that would otherwise irrigate, plus alert helpers."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_db: IrrigationRepository) -> None:
        self.db = tmp_db
        self.logic = IrrigationLogic(self.db, get_plant_database())
        self.cluster_id = self.db.add_cluster("Leak Hold Cluster")
        self.db.add_plant(cluster_id=self.cluster_id, species=FAKE_PLANT_SPECIES, water_needs="medium")
        self.irrigator_id = self.db.add_irrigator(
            cluster_id=self.cluster_id,
            tuya_device_id="fake_irr_hold",
            name="Irrigator",
            irrigator_type="rainpoint.ik10pw",
            config={},
        )
        self.sensor_id = self.db.add_sensor(
            cluster_id=self.cluster_id,
            tuya_device_id="fake_sensor_hold",
            name="Dry Sensor",
            sensor_type="tuya.tr301z",
            config={},
        )
        # Bone dry: without a hold this cluster irrigates.
        self.db.add_sensor_reading(sensor_id=self.sensor_id, soil_moisture=20.0)

    def _raise_leak(self, *, cluster_id: int | None = None, seen_at: int | None = None, status: str = "open") -> Alert:
        alert = self.db.upsert_alert(
            dedup_key=f"leak::{LEAK_ALERT_CODE}::{cluster_id or self.cluster_id}::sensor{self.sensor_id}",
            source=ActivitySource.LEAK,
            code=LEAK_ALERT_CODE,
            title="Possible leak or stuck valve detected",
            message="Dry Sensor: soil moisture still rising after irrigation (latest=78.0%)",
            severity="critical",
            entity_type=EntityType.SENSOR,
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
    """An open leak alert holds automatic irrigation (invariant 11)."""

    def test_dry_cluster_irrigates_without_a_hold(self) -> None:
        """Baseline — the fixture is only meaningful if it irrigates untouched."""
        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision is not None

        assert decision.action == Action.IRRIGATE

    def test_open_leak_alert_holds_the_cluster(self) -> None:
        """An open leak alert turns an irrigate decision into a hold."""
        self._raise_leak()

        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision is not None

        assert decision.action == Action.SKIP
        assert decision.primary_code == TriggerCode.LEAK_HOLD
        assert decision.duration_minutes == 0

    def test_hold_reason_is_critical_and_names_the_alert(self) -> None:
        """The hold is the critical, terminal reason and names the alert."""
        alert = self._raise_leak()

        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision is not None
        reason = decision.reasons[0]

        assert reason.severity == Severity.CRITICAL
        assert f"#{alert.id}" in reason.message
        assert "still rising" in reason.message

    def test_hold_outranks_cooldown(self) -> None:
        """Both active → report the safety gate, not the routine one."""
        self._raise_leak()
        self.db.add_irrigation_event(
            irrigator_id=self.irrigator_id,
            action=EventAction.START,
            triggered_by=TriggeredBy.AUTO,
            duration_minutes=2,
            timestamp=int(time.time()) - 3600,
        )

        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision is not None

        assert decision.primary_code == TriggerCode.LEAK_HOLD

    def test_hold_is_persisted_to_the_decision_log(self) -> None:
        """A persisted evaluation records the hold in the decision log."""
        self._raise_leak()

        self.logic.decide_for_cluster(self.cluster_id, persist=True)

        logs = self.db.list_decision_logs(cluster_id=self.cluster_id)
        assert logs[0].primary_code == TriggerCode.LEAK_HOLD.value
        assert logs[0].actuated is False


class TestHoldLifts(_Fixture):
    """What releases a leak hold."""

    def test_hold_expires_after_the_configured_window(self) -> None:
        """The hold expires after ``LEAK_HOLD_HOURS``."""
        self._raise_leak(seen_at=int(time.time()) - (LEAK_HOLD_HOURS * 3600 + 60))

        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision is not None

        assert decision.action == Action.IRRIGATE

    def test_alert_on_another_cluster_does_not_hold_this_one(self) -> None:
        """An alert on another cluster does not hold this one."""
        other = self.db.add_cluster("Other Cluster")
        self._raise_leak(cluster_id=other)

        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision is not None

        assert decision.action == Action.IRRIGATE


class TestGetActiveAlert(_Fixture):
    """Repository helper the gate is built on."""

    def test_returns_none_when_no_alert_exists(self) -> None:
        """No alert means no active alert."""
        assert self.db.get_active_alert(LEAK_ALERT_CODE, cluster_id=self.cluster_id) is None

    def test_ignores_resolved_alerts(self) -> None:
        """Resolved alerts are not active."""
        self._raise_leak(status="resolved")

        assert self.db.get_active_alert(LEAK_ALERT_CODE, cluster_id=self.cluster_id) is None

    def test_since_bounds_how_stale_an_alert_may_be(self) -> None:
        """``since`` excludes alerts last seen before it."""
        now = int(time.time())
        self._raise_leak(seen_at=now - 7200)

        assert self.db.get_active_alert(LEAK_ALERT_CODE, cluster_id=self.cluster_id, since=now - 3600) is None
        assert self.db.get_active_alert(LEAK_ALERT_CODE, cluster_id=self.cluster_id, since=now - 10800) is not None

    def test_ignores_other_codes(self) -> None:
        """Alerts with other codes are not returned."""
        self.db.upsert_alert(
            dedup_key="anomaly::sensor_drift::1::sensor1",
            source=ActivitySource.ANOMALY,
            code="sensor_drift",
            title="Drift",
            message="drifting",
            severity="warning",
            cluster_id=self.cluster_id,
        )

        assert self.db.get_active_alert(LEAK_ALERT_CODE, cluster_id=self.cluster_id) is None
