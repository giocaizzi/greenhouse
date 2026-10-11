"""The maintenance ``low_light`` check picks its light season in the preferences timezone.

The decision engine reads the seasonal light factor for the month in
``UserPreferences.timezone``; the maintenance scan must use the same clock, so
around a month boundary both judge light against the same seasonal minimum.
"""

from __future__ import annotations

from datetime import UTC, datetime

import time_machine

from greenhouse_server.services.maintenance import collect_maintenance_alerts

# 2026-05-31 23:30 UTC is already 2026-06-01 01:30 in Europe/Rome:
# May's light factor is 0.95, June's is 1.00 (constants.SEASONAL_LIGHT_FACTOR_BY_MONTH).
_MONTH_BOUNDARY = datetime(2026, 5, 31, 23, 30, tzinfo=UTC)


class _CarePlantDB:
    """Plant-DB stand-in: every plant needs the same minimum light."""

    def get_care_data(self, species=None, category=None) -> dict:
        return {"ideal_light_lux_min": 5000}


def _dim_cluster(repo, now: int) -> int:
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


def _low_light_message(repo, cid: int) -> str:
    (finding,) = [a for a in collect_maintenance_alerts(repo, cid, _CarePlantDB()) if a["type"] == "low_light"]
    return finding["message"]


def test_low_light_season_uses_the_preferences_timezone_month(tmp_db):
    with time_machine.travel(_MONTH_BOUNDARY, tick=False):
        cid = _dim_cluster(tmp_db, int(_MONTH_BOUNDARY.timestamp()))
        tmp_db.update_preferences(timezone="Europe/Rome")
        tmp_db.session.commit()
        assert _low_light_message(tmp_db, cid) == "Season Sensor: avg 100 lux (seasonal min 5000)"


def test_low_light_season_defaults_to_utc(tmp_db):
    with time_machine.travel(_MONTH_BOUNDARY, tick=False):
        cid = _dim_cluster(tmp_db, int(_MONTH_BOUNDARY.timestamp()))
        assert _low_light_message(tmp_db, cid) == "Season Sensor: avg 100 lux (seasonal min 4750)"
