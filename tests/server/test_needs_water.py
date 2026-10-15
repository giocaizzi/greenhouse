"""Sensor-only clusters raise a ``needs_water`` alert (and push) when a sensor stays dry (issue #142)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from engine_grid import plant_database
from golden import FROZEN_TS
from greenhouse_core.constants import NEEDS_WATER_PERSIST_HOURS, SECONDS_PER_HOUR
from greenhouse_server.services.irrigation import IrrigationService
from greenhouse_server.services.needs_water import NEEDS_WATER_CODE
from greenhouse_server.services.notify import NtfyClient
from greenhouse_server.services.sync import SyncService

if TYPE_CHECKING:
    from pathlib import Path

    import time_machine

    from greenhouse_core.models import Alert
    from greenhouse_core.repository import IrrigationRepository
    from greenhouse_server.services.weather import WeatherClient

# Monstera deliciosa: 45-60 % band, very dry below 30 %.
DRY = 40.0
VERY_DRY = 20.0
IN_BAND = 50.0
PERSIST = NEEDS_WATER_PERSIST_HOURS * SECONDS_PER_HOUR
HALF_HOUR = SECONDS_PER_HOUR // 2


class _RecordingNotifier(NtfyClient):
    """Records alert pushes instead of publishing (never calls ``NtfyClient.__init__``)."""

    def __init__(self) -> None:
        self.alerts: list[dict[str, object]] = []

    def notify_alert(self, **kwargs: object) -> bool:
        self.alerts.append(kwargs)
        return True


@pytest.fixture
def db(tmp_db: IrrigationRepository, frozen_clock: time_machine.Traveller, clean_env: Path) -> IrrigationRepository:
    """The in-memory repository, with a clean environment and a frozen clock."""
    del frozen_clock, clean_env  # requested only for their side effects
    return tmp_db


def _sensor_only_cluster(db: IrrigationRepository) -> tuple[int, int]:
    cid = db.add_cluster("Windowsill")
    pid = db.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical")
    sid = db.add_sensor(
        cluster_id=cid,
        tuya_device_id="fake_tuya_sensor_nw000001",
        name="Pot Probe",
        sensor_type="tuya.tr301z",
        config={},
        plant_id=pid,
    )
    return cid, sid


def _readings(db: IrrigationRepository, sid: int, soil: float, *, hours: float, until: int = FROZEN_TS) -> None:
    """One reading every half hour from ``hours`` ago up to ``until``."""
    start = until - int(hours * SECONDS_PER_HOUR)
    for ts in range(start, until + 1, HALF_HOUR):
        db.add_sensor_reading(sensor_id=sid, timestamp=ts, soil_moisture=soil)


def _check(db: IrrigationRepository, cid: int, notifier: _RecordingNotifier | None = None) -> None:
    sync = SyncService(db, None, None)
    weather = cast("WeatherClient", None)
    IrrigationService(db, None, sync, weather, plant_database(), notifier=notifier).check_cluster(cid)


def _needs_water(db: IrrigationRepository, cid: int) -> list[Alert]:
    return [a for a in db.list_alerts(cluster_id=cid) if a.code == NEEDS_WATER_CODE]


def test_a_sensor_dry_for_the_persist_window_opens_one_alert_and_pushes(db: IrrigationRepository) -> None:
    """Dry since before the window: one warning alert, pushed once, not again on the next check."""
    cid, sid = _sensor_only_cluster(db)
    _readings(db, sid, DRY, hours=NEEDS_WATER_PERSIST_HOURS + 1)
    notifier = _RecordingNotifier()
    _check(db, cid, notifier)
    _check(db, cid, notifier)
    (alert,) = _needs_water(db, cid)
    assert (alert.status, alert.severity, alert.entity_id) == ("open", "warning", sid)
    assert "Pot Probe" in alert.message
    assert len(notifier.alerts) == 1


def test_very_dry_is_critical(db: IrrigationRepository) -> None:
    """A sensor more than the very-dry margin below its band opens a critical alert."""
    cid, sid = _sensor_only_cluster(db)
    _readings(db, sid, VERY_DRY, hours=NEEDS_WATER_PERSIST_HOURS + 1)
    _check(db, cid)
    (alert,) = _needs_water(db, cid)
    assert alert.severity == "critical"


def test_a_short_dry_spell_raises_nothing(db: IrrigationRepository) -> None:
    """In band until an hour ago: the dry call has not held for the persist window yet."""
    cid, sid = _sensor_only_cluster(db)
    _readings(db, sid, IN_BAND, hours=NEEDS_WATER_PERSIST_HOURS + 1, until=FROZEN_TS - 2 * SECONDS_PER_HOUR)
    _readings(db, sid, DRY, hours=1)
    _check(db, cid)
    assert _needs_water(db, cid) == []


def test_watering_resolves_the_alert(db: IrrigationRepository) -> None:
    """A soil jump back into the band resolves the alert on the next check."""
    cid, sid = _sensor_only_cluster(db)
    _readings(db, sid, DRY, hours=NEEDS_WATER_PERSIST_HOURS + 1, until=FROZEN_TS - SECONDS_PER_HOUR)
    _check(db, cid)
    (alert,) = _needs_water(db, cid)
    _readings(db, sid, IN_BAND, hours=0.5)
    _check(db, cid)
    db.session.refresh(alert)
    assert alert.status == "resolved"


def test_clusters_with_an_irrigator_get_no_reminder(db: IrrigationRepository) -> None:
    """The pipeline waters irrigated clusters, so the monitor's reminder is not raised there."""
    cid, sid = _sensor_only_cluster(db)
    db.add_irrigator(cid, "fake_tuya_device_nw000002", "Pump", "rainpoint.ik10pw", {})
    db.set_irrigation_config(cid, auto_run=False)
    _readings(db, sid, DRY, hours=NEEDS_WATER_PERSIST_HOURS + 1)
    _check(db, cid)
    assert _needs_water(db, cid) == []


def test_quiet_hours_hold_the_alert_back(db: IrrigationRepository) -> None:
    """No alert (and no push) is opened inside the cluster's quiet hours; 10:00 UTC is quiet here."""
    cid, sid = _sensor_only_cluster(db)
    db.set_irrigation_config(cid, quiet_start_hour=9, quiet_end_hour=11)
    _readings(db, sid, DRY, hours=NEEDS_WATER_PERSIST_HOURS + 1)
    notifier = _RecordingNotifier()
    _check(db, cid, notifier)
    assert _needs_water(db, cid) == []
    assert notifier.alerts == []


def test_the_notify_alerts_preference_mutes_the_push(db: IrrigationRepository) -> None:
    """The alert still opens, but ``notify_alerts`` off means no push."""
    cid, sid = _sensor_only_cluster(db)
    db.update_preferences(notify_alerts=False)
    _readings(db, sid, DRY, hours=NEEDS_WATER_PERSIST_HOURS + 1)
    notifier = _RecordingNotifier()
    _check(db, cid, notifier)
    assert len(_needs_water(db, cid)) == 1
    assert notifier.alerts == []


def test_a_removed_sensor_resolves_its_alert(db: IrrigationRepository) -> None:
    """A sensor that leaves the cluster does not leave an alert open forever."""
    cid, sid = _sensor_only_cluster(db)
    _readings(db, sid, DRY, hours=NEEDS_WATER_PERSIST_HOURS + 1)
    _check(db, cid)
    (alert,) = _needs_water(db, cid)
    db.delete_sensor(sid)
    _check(db, cid)
    db.session.refresh(alert)
    assert alert.status == "resolved"
