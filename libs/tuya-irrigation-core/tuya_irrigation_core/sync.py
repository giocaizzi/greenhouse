"""Sensor data sync logic.

Syncs sensor data from Tuya Cloud to local DB:
1. Pulls historical logs (getdevicelog) — backfills any gaps
2. Gets live reading (getstatus) — latest state
3. Deduplicates by (sensor_id, timestamp) — no duplicates ever

Tuya Cloud is the source of truth for sensor data.
Local DB is our permanent archive.
"""

import logging
import time

from tuya_irrigation_core.cloud import TuyaCloud
from tuya_irrigation_core.repository import IrrigationRepository

logger = logging.getLogger(__name__)


def sync_sensor_data(db: IrrigationRepository, cloud: TuyaCloud, hours: int = 24) -> dict:
    """Sync all sensor data from Tuya Cloud to local DB.

    Returns dict with sync stats per cluster.
    """
    clusters = db.list_clusters()
    stats = {"total_synced": 0, "total_new": 0, "total_live": 0, "errors": []}

    for cluster in clusters:
        sensors = db.get_sensors_in_cluster(cluster.id)
        if not sensors:
            continue

        logger.info("[%s] Syncing %d sensor(s)...", cluster.name, len(sensors))

        for sensor in sensors:
            try:
                synced, new, live = sync_single_sensor(db, cloud, sensor, hours)
                stats["total_synced"] += synced
                stats["total_new"] += new
                stats["total_live"] += live

                parts = []
                if new > 0:
                    parts.append(f"{new} new from logs")
                if live:
                    parts.append("live ✓")
                if not parts:
                    parts.append("up to date")

                logger.info("  %s: %s", sensor.name, ", ".join(parts))

            except Exception as e:
                stats["errors"].append(f"{sensor.name}: {e}")
                logger.error("  %s: %s", sensor.name, e)

    return stats


def sync_single_sensor(db: IrrigationRepository, cloud: TuyaCloud, sensor, hours: int) -> tuple[int, int, int]:
    """Sync a single sensor. Returns (total_processed, new_inserted, live_saved)."""

    # 1. Determine sync window
    last_ts = db.get_last_reading_timestamp(sensor.id)
    if last_ts:
        # Sync from last known reading (with 1min overlap for safety)
        since_ms = (last_ts - 60) * 1000
    else:
        # First sync: pull full history window
        since_ms = int((time.time() - hours * 3600) * 1000)

    # 2. Pull historical logs from cloud
    logs = cloud.get_device_logs(sensor.tuya_device_id, since_ms=since_ms)
    grouped = cloud.group_logs_by_timestamp(logs)

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

    # 3. Get live reading (current state)
    live_saved = 0
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
                live_saved = 1
    except Exception:
        pass  # Live reading is best-effort

    return len(grouped), new_count, live_saved
