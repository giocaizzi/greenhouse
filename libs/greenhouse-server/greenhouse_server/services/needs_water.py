"""Watering reminders for sensor-only clusters: the monitor's dry call becomes an inbox alert and a push.

A cluster without an irrigator is only watched: ``monitor_cluster`` classifies each
sensor's newest cleaned soil reading against its plant's band. A sensor that has
read ``dry`` / ``very_dry`` for ``NEEDS_WATER_PERSIST_HOURS`` opens one
``needs_water`` alert (warning; critical when very dry), pushed through the usual
alert path (``notify_alerts`` preference). It is not opened during the cluster's
quiet hours (the next check outside them does it), and it resolves once the sensor
reads in band again.
"""

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, TypedDict

from greenhouse_core.constants import (
    MONITOR_VERY_DRY_MARGIN,
    MONITOR_WET_MARGIN,
    NEEDS_WATER_PERSIST_HOURS,
    SECONDS_PER_HOUR,
    SNAPSHOT_LOOKBACK_HOURS,
)
from greenhouse_core.logic.cleaning import clean_readings_desc
from greenhouse_core.logic.timing import active_quiet_window
from greenhouse_core.models import ENTITY_SENSOR, SOURCE_SENSOR
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import clear_alert, raise_alert
from greenhouse_server.services.notify import sensor_label

if TYPE_CHECKING:
    from greenhouse_server.services.notify import NtfyClient

NEEDS_WATER_CODE = "needs_water"
NEEDS_WATER_STATUSES = ("very_dry", "dry")
_SETTLED_STATUSES = ("ok", "wet")


class MonitorSensorRow(TypedDict):
    """One ``MonitorResult.sensors`` row: the sensor's latest cleaned soil value against its target band."""

    sensor_id: int
    sensor_name: str
    plant_species: str | None
    soil_moisture: float | None
    status: str
    target_min: float
    target_max: float


def soil_status(latest_soil: float | None, t_min: float, t_max: float) -> str:
    """Classify one sensor's latest soil value against the target band."""
    if latest_soil is None:
        return "no_data"
    if latest_soil < t_min - MONITOR_VERY_DRY_MARGIN:
        return "very_dry"
    if latest_soil < t_min:
        return "dry"
    if latest_soil > t_max + MONITOR_WET_MARGIN:
        return "wet"
    return "ok"


def _dry_long_enough(repo: IrrigationRepository, row: MonitorSensorRow, now: int) -> bool:
    """Whether every cleaned soil reading since ``NEEDS_WATER_PERSIST_HOURS`` ago, and the last one before, is dry.

    The reading before the cutoff proves the dry spell is at least that long, not just a short history.
    """
    cutoff = now - NEEDS_WATER_PERSIST_HOURS * SECONDS_PER_HOUR
    history = clean_readings_desc(repo.get_recent_readings(row["sensor_id"], hours=SNAPSHOT_LOOKBACK_HOURS))
    soil = [(r.timestamp, r.soil_moisture) for r in history if r.soil_moisture is not None]
    held = [value for ts, value in soil if ts >= cutoff]
    before = next((value for ts, value in soil if ts < cutoff), None)
    if before is None:
        return False
    band = (row["target_min"], row["target_max"])
    return all(soil_status(value, *band) in NEEDS_WATER_STATUSES for value in [*held, before])


def _raise(repo: IrrigationRepository, cluster_id: int, row: MonitorSensorRow, notifier: "NtfyClient | None") -> None:
    """Open (or refresh) the sensor's ``needs_water`` alert."""
    sensor = repo.get_sensor(row["sensor_id"])
    label = sensor_label(sensor) if sensor else row["sensor_name"]
    raise_alert(
        repo,
        notifier=notifier,
        source=SOURCE_SENSOR,
        code=NEEDS_WATER_CODE,
        severity="critical" if row["status"] == "very_dry" else "warning",
        title=f"Needs water · {label}",
        message=(
            f"{label}: soil {row['soil_moisture']:.0f}%, below its "
            f"{row['target_min']:.0f}-{row['target_max']:.0f}% band for over {NEEDS_WATER_PERSIST_HOURS} h. "
            "This cluster has no irrigator: water by hand."
        ),
        cluster_id=cluster_id,
        sensor_id=row["sensor_id"],
    )


def _resolve_orphans(repo: IrrigationRepository, cluster_id: int, watched: set[int]) -> None:
    """Resolve open ``needs_water`` alerts of sensors no longer watched (removed, or the cluster got an irrigator)."""
    for alert in repo.list_alerts(cluster_id=cluster_id):
        is_orphan = alert.entity_type == ENTITY_SENSOR and alert.entity_id not in watched
        if alert.code == NEEDS_WATER_CODE and alert.status != "resolved" and is_orphan:
            repo.resolve_alert(alert.id)


def reconcile_needs_water_alerts(
    repo: IrrigationRepository,
    cluster_id: int,
    sensors: Sequence[MonitorSensorRow],
    *,
    notifier: "NtfyClient | None" = None,
) -> None:
    """Turn the monitor's per-sensor call into ``needs_water`` alerts; ``sensors`` is empty for an irrigated cluster.

    Reuses the monitor's classification (``status``) and band; a sensor with no data keeps its alert as it is.
    """
    now = int(time.time())
    prefs = repo.get_preferences()
    tz_name = prefs.timezone if prefs else None
    quiet = active_quiet_window(repo.get_effective_config(cluster_id), now_unix=now, tz_name=tz_name)
    for row in sensors:
        sensor_id = row["sensor_id"]
        if row["status"] in _SETTLED_STATUSES:
            clear_alert(repo, source=SOURCE_SENSOR, code=NEEDS_WATER_CODE, cluster_id=cluster_id, sensor_id=sensor_id)
        elif row["status"] in NEEDS_WATER_STATUSES and quiet is None and _dry_long_enough(repo, row, now):
            _raise(repo, cluster_id, row, notifier)
    _resolve_orphans(repo, cluster_id, {row["sensor_id"] for row in sensors})
