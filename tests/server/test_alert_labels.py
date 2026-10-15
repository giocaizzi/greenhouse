"""Sensor alerts name the plant and cluster, not just the sensor; their dedup keys stay put."""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine_grid import plant_database
from greenhouse_server.services import alerts as alerts_mod
from greenhouse_server.services.alerts import sync_cluster_alerts

if TYPE_CHECKING:
    import pytest

    from greenhouse_core.repository import IrrigationRepository
    from greenhouse_server.services.maintenance import AlertFinding

DRAINAGE = "💨 Sensore 2: rapid drainage (-5.5%/hr). Soil may not retain water well."


def _learning_finds(monkeypatch: pytest.MonkeyPatch, *messages: str) -> None:
    """Make the learner report ``messages`` as ``rapid_drainage`` and maintenance report nothing."""
    found: list[AlertFinding] = [{"severity": "warning", "type": "rapid_drainage", "message": m} for m in messages]
    monkeypatch.setattr(alerts_mod, "collect_learning_alerts", lambda *_args: found)
    monkeypatch.setattr(alerts_mod, "collect_maintenance_alerts", lambda *_args: [])


def test_learning_alert_names_plant_and_cluster_and_keeps_its_key(
    tmp_db: IrrigationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Title and message carry "<species> (<cluster>) · <sensor>"; moving the sensor keeps the same row."""
    cid = tmp_db.add_cluster("Sala Angolo")
    dracaena = tmp_db.add_plant(cid, "Dracaena")
    sid = tmp_db.add_sensor(cid, "fake_tuya_device_lbl1", "Sensore 2", "tuya.tr301z", {}, plant_id=dracaena)
    tmp_db.add_sensor(cid, "fake_tuya_device_lbl2", "Sensore 20", "tuya.tr301z", {})
    _learning_finds(monkeypatch, DRAINAGE, "💨 Sensore 20: rapid drainage (-6.0%/hr).")

    first, unplanted = sorted(sync_cluster_alerts(tmp_db, cid, plant_database()), key=lambda a: a.id)
    assert first.title == "Rapid Drainage · 💨 Dracaena (Sala Angolo) · Sensore 2"
    assert (
        first.message
        == "💨 Dracaena (Sala Angolo) · Sensore 2: rapid drainage (-5.5%/hr). Soil may not retain water well."
    )
    assert unplanted.title == "Rapid Drainage · 💨 Sala Angolo · Sensore 20"
    assert first.dedup_key == f"learning::rapid_drainage::{cid}::💨 sensore 2"  # the raw sensor head, as before

    tmp_db.update_sensor(sid, plant_id=None)
    (again, _) = sorted(sync_cluster_alerts(tmp_db, cid, plant_database()), key=lambda a: a.id)
    assert (again.id, again.status, again.title) == (first.id, "open", "Rapid Drainage · 💨 Sala Angolo · Sensore 2")


def test_finding_without_a_sensor_head_is_left_alone(
    tmp_db: IrrigationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cluster-level finding (the conflict alert) names no single sensor and keeps its wording."""
    cid = tmp_db.add_cluster("Sala Angolo")
    tmp_db.add_sensor(cid, "fake_tuya_device_lbl3", "Sensore 2", "tuya.tr301z", {})
    conflict = "⚠️ Unresolvable conflict: Sensore 2 needs ~5min of irrigation."
    _learning_finds(monkeypatch, conflict)

    (alert,) = sync_cluster_alerts(tmp_db, cid, plant_database())
    assert (alert.title, alert.message) == ("Rapid Drainage · ⚠️ Unresolvable conflict", conflict)
