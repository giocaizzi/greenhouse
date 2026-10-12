"""The maintenance ``low_light`` check picks its light season in the preferences timezone.

The decision engine reads the seasonal light factor for the month in
``UserPreferences.timezone``; the maintenance scan must use the same clock, so
around a month boundary both judge light against the same seasonal minimum.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import time_machine

from greenhouse_server.services.maintenance import collect_maintenance_alerts

if TYPE_CHECKING:
    from greenhouse_core.plant_db import PlantDatabase
    from greenhouse_core.repository import IrrigationRepository

# 2026-05-31 23:30 UTC is already 2026-06-01 01:30 in Europe/Rome:
# May's light factor is 0.95, June's is 1.00 (constants.SEASONAL_LIGHT_FACTOR_BY_MONTH).
_MONTH_BOUNDARY = datetime(2026, 5, 31, 23, 30, tzinfo=UTC)


class _CarePlantDB:
    """Plant-DB stand-in: every plant needs the same minimum light."""

    def get_care_data(self, species: str | None = None, category: str | None = None) -> dict[str, int]:
        """Return the same care data whatever the plant."""
        del species, category  # every plant gets the same minimum
        return {"ideal_light_lux_min": 5000}


def _dim_cluster(repo: IrrigationRepository, now: int) -> int:
    cid = repo.add_cluster("Season Cluster")
    pid = repo.add_plant(cluster_id=cid, species="Gapus plantus", category="tropical")
    sid = repo.add_sensor(
        cluster_id=cid,
        tuya_device_id="season-sensor",
        name="Season Sensor",
        sensor_type="tuya.tr301z",
        config={},
        plant_id=pid,
        assignment_started_at=now - 30 * 86400,
    )
    for k in range(3):
        repo.add_sensor_reading(sensor_id=sid, timestamp=now - 600 * (k + 1), light=100)
    repo.session.commit()
    return cid


def _low_light_message(repo: IrrigationRepository, cid: int) -> str:
    # Duck-typed stand-in: the scan reads only ``get_care_data``.
    plant_db = cast("PlantDatabase", _CarePlantDB())
    (finding,) = [a for a in collect_maintenance_alerts(repo, cid, plant_db) if a["type"] == "low_light"]
    return finding["message"]


def test_low_light_season_uses_the_preferences_timezone_month(tmp_db: IrrigationRepository) -> None:
    """Already June in Europe/Rome: the low-light minimum uses June's factor (1.00)."""
    with time_machine.travel(_MONTH_BOUNDARY, tick=False):
        cid = _dim_cluster(tmp_db, int(_MONTH_BOUNDARY.timestamp()))
        tmp_db.update_preferences(timezone="Europe/Rome")
        tmp_db.session.commit()
        assert _low_light_message(tmp_db, cid) == "Season Sensor: avg 100 lux (seasonal min 5000)"


def test_low_light_season_defaults_to_utc(tmp_db: IrrigationRepository) -> None:
    """Without a timezone preference the month is UTC's: still May (factor 0.95)."""
    with time_machine.travel(_MONTH_BOUNDARY, tick=False):
        cid = _dim_cluster(tmp_db, int(_MONTH_BOUNDARY.timestamp()))
        assert _low_light_message(tmp_db, cid) == "Season Sensor: avg 100 lux (seasonal min 4750)"
