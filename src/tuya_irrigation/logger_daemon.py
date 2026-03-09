#!/usr/bin/env python3
"""Sensor data sync daemon.

Syncs sensor data from Tuya Cloud to local SQLite:
1. Pulls historical logs (getdevicelog) — backfills any gaps
2. Gets live reading (getstatus) — latest state
3. Deduplicates by (sensor_id, timestamp) — no duplicates ever

Tuya Cloud is the source of truth for sensor data.
Local DB is our permanent archive.
"""

import argparse
import sys
import time
from pathlib import Path

from tuya_irrigation.cloud import TuyaCloud
from tuya_irrigation.db import IrrigationDB


def sync_sensor_data(db: IrrigationDB, cloud: TuyaCloud, hours: int = 24) -> dict:
    """Sync all sensor data from Tuya Cloud to local DB.

    Returns dict with sync stats per cluster.
    """
    clusters = db.list_clusters()
    stats = {"total_synced": 0, "total_new": 0, "total_live": 0, "errors": []}

    for cluster in clusters:
        sensors = db.get_sensors_in_cluster(cluster.id)
        if not sensors:
            continue

        print(f"[{cluster.name}] Syncing {len(sensors)} sensor(s)...")

        for sensor in sensors:
            try:
                synced, new, live = _sync_single_sensor(db, cloud, sensor, hours)
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

                print(f"  ✅ {sensor.name}: {', '.join(parts)}")

            except Exception as e:
                stats["errors"].append(f"{sensor.name}: {e}")
                print(f"  ❌ {sensor.name}: {e}")

    return stats


def _sync_single_sensor(db: IrrigationDB, cloud: TuyaCloud, sensor, hours: int) -> tuple[int, int, int]:
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
            humidity=reading.get("humidity"),
            soil_moisture=reading.get("soil_moisture"),
            light=reading.get("light"),
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
                humidity=live.get("humidity"),
                soil_moisture=live.get("soil_moisture"),
                light=live.get("light"),
            )
            if result is not None:
                live_saved = 1
    except Exception:
        pass  # Live reading is best-effort

    return len(grouped), new_count, live_saved


def main():
    parser = argparse.ArgumentParser(description="Sensor data sync (Tuya Cloud → local DB)")
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Run continuously with this interval in minutes (0 = run once)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Hours of history to sync on first run (default: 24)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Database path",
    )
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else None
    db = IrrigationDB(db_path)
    cloud = TuyaCloud()

    if args.interval <= 0:
        # Run once
        stats = sync_sensor_data(db, cloud, hours=args.hours)
        print(f"\n📊 Synced: {stats['total_new']} new readings")
        if stats["errors"]:
            print(f"⚠️  Errors: {len(stats['errors'])}")
        db.close()
        return 0

    # Continuous mode
    print(f"🔄 Starting continuous sync (every {args.interval} minutes)")
    print("   Press Ctrl+C to stop")

    try:
        while True:
            stats = sync_sensor_data(db, cloud, hours=args.hours)
            print(f"\n📊 Synced: {stats['total_new']} new readings")
            print(f"   Next sync in {args.interval} minutes...\n")
            time.sleep(args.interval * 60)
    except KeyboardInterrupt:
        print("\n⏹️  Stopped by user")
        db.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
