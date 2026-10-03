"""Sensor data sync logic.

Syncs sensor data from Tuya Cloud to local DB:
1. Pulls historical logs (getdevicelog) — backfills any gaps
2. Gets live reading (getstatus) — latest state
3. Deduplicates by (sensor_id, timestamp) — no duplicates ever

Tuya Cloud is the source of truth for sensor data.
Local DB is our permanent archive.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from greenhouse_core.devices.gateway import DeviceGateway
from greenhouse_core.repository import IrrigationRepository

if TYPE_CHECKING:
    from greenhouse_core.models import Sensor

logger = logging.getLogger(__name__)


def sync_sensor_data(db: IrrigationRepository, cloud: DeviceGateway, hours: int = 24) -> dict[str, Any]:
    """Sync all sensor data from Tuya Cloud to local DB.

    Returns dict with sync stats per cluster.
    """
    clusters = db.list_clusters()
    stats: dict[str, Any] = {"total_synced": 0, "total_new": 0, "total_live": 0, "errors": []}

    for cluster in clusters:
        sensors = db.get_sensors_in_cluster(cluster.id)
        if not sensors:
            continue

        logger.info("[%s] Syncing %d sensor(s)...", cluster.name, len(sensors))

        for sensor in sensors:
            _sync_logged(db, cloud, sensor, hours, stats)

    return stats


def _sync_logged(
    db: IrrigationRepository, cloud: DeviceGateway, sensor: Sensor, hours: int, stats: dict[str, Any]
) -> None:
    """Sync one sensor into the running ``stats`` totals and log its summary.

    The per-sensor ``try`` is the isolation boundary: any failure is recorded as
    ``"<name>: <error>"``, logged at ERROR, and never stops the loop over sensors.
    """
    try:
        synced, new, live = sync_single_sensor(db, cloud, sensor, hours)
        stats["total_synced"] += synced
        stats["total_new"] += new
        stats["total_live"] += live

        logger.info("  %s: %s", sensor.name, _sync_summary(new, live))

    except Exception as e:  # noqa: BLE001 — per-sensor isolation: record, log, keep looping
        stats["errors"].append(f"{sensor.name}: {e}")
        logger.error("  %s: %s", sensor.name, e)


def _sync_summary(new: int, live: int) -> str:
    """Return the per-sensor log summary, e.g. ``"2 new from logs, live ✓"``."""
    parts = []
    if new > 0:
        parts.append(f"{new} new from logs")
    if live:
        parts.append("live ✓")
    if not parts:
        parts.append("up to date")
    return ", ".join(parts)


def sync_single_sensor(
    db: IrrigationRepository, cloud: DeviceGateway, sensor: Sensor, hours: int
) -> tuple[int, int, int]:
    """Sync a single sensor. Returns (total_processed, new_inserted, live_saved).

    Order (invariant 8): sync window -> one ``getdevicelog`` pull -> per-reading
    insert -> exactly one best-effort live read.
    """
    since_ms = _sync_window_start(db.get_last_reading_timestamp(sensor.id), hours, time.time())
    logs = cloud.get_device_logs(sensor.tuya_device_id, since_ms=since_ms)
    grouped = cloud.group_logs_by_timestamp(logs)
    new_count = _store_history(db, sensor, grouped)
    live_saved = int(_store_live_reading(db, cloud, sensor))
    return len(grouped), new_count, live_saved


def _sync_window_start(last_ts: int | None, hours: int, now: float) -> int:
    """Return the ``getdevicelog`` window start in epoch milliseconds.

    With a stored reading, resume one minute before it (overlap for safety; the
    repository de-duplicates on ``(sensor_id, timestamp)``). On the first sync
    (no row, or a falsy timestamp) pull the full ``hours`` window back from ``now``.
    """
    if last_ts:
        return (last_ts - 60) * 1000
    return int((now - hours * 3600) * 1000)


def _store_history(db: IrrigationRepository, sensor: Sensor, grouped: list[dict[str, Any]]) -> int:
    """Insert each grouped log reading; return how many rows were new.

    Readings without a timestamp are skipped. Log rows never carry
    ``water_warning`` — only the live read persists it.
    """
    new_count = 0
    for reading in grouped:
        ts = reading.get("timestamp")
        if not ts:
            continue

        result = db.add_sensor_reading(
            sensor_id=sensor.id,
            timestamp=ts,
            temperature=reading.get("temperature"),
            soil_moisture=reading.get("soil_moisture"),
            light=reading.get("light"),
            env_humidity=reading.get("env_humidity"),
            battery_state=reading.get("battery_state"),
        )
        if result is not None:
            new_count += 1
    return new_count


def _store_live_reading(db: IrrigationRepository, cloud: DeviceGateway, sensor: Sensor) -> bool:
    """Persist the sensor's current state at now; ``True`` when a new row was stored.

    The single live read is best-effort: any error is swallowed and counts as
    "not saved", so a flaky live endpoint never fails the backfill above.
    """
    saved = False
    try:
        live = cloud.get_live_reading(sensor.tuya_device_id)
        if live and any(k in live for k in ("temperature", "soil_moisture", "humidity", "light")):
            now = int(time.time())
            result = db.add_sensor_reading(
                sensor_id=sensor.id,
                timestamp=now,
                temperature=live.get("temperature"),
                soil_moisture=live.get("soil_moisture"),
                light=live.get("light"),
                env_humidity=live.get("env_humidity"),
                battery_state=live.get("battery_state"),
                water_warning=live.get("water_warning"),
            )
            if result is not None:
                saved = True
    except Exception:  # noqa: BLE001, S110 — the live read is best-effort by contract (invariant 8)
        pass  # Live reading is best-effort
    return saved
