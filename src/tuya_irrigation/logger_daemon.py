#!/usr/bin/env python3
"""Sensor data logging daemon."""

import argparse
import sys
import time
from pathlib import Path

from tuya_irrigation.db import IrrigationDB
from tuya_irrigation.devices import TuyaDeviceManager


def log_sensor_readings(db: IrrigationDB, device_manager: TuyaDeviceManager):
    """Read all sensors and log their data."""
    clusters = db.list_clusters()
    total_readings = 0

    for cluster in clusters:
        sensors = db.get_sensors_in_cluster(cluster.id)
        if not sensors:
            continue

        print(f"[{cluster.name}] Reading {len(sensors)} sensor(s)...")

        for sensor in sensors:
            try:
                data = device_manager.read_sensor(sensor)

                if "error" in data:
                    print(f"  ❌ {sensor.name}: {data['error']}")
                    continue

                # Log to database
                db.add_sensor_reading(
                    sensor_id=sensor.id,
                    temperature=data.get("temperature"),
                    humidity=data.get("humidity"),
                    soil_moisture=data.get("soil_moisture"),
                    light=data.get("light"),
                )

                # Print summary
                parts = []
                if data.get("temperature") is not None:
                    parts.append(f"temp={data['temperature']:.1f}°C")
                if data.get("humidity") is not None:
                    parts.append(f"hum={data['humidity']:.0f}%")
                if data.get("soil_moisture") is not None:
                    parts.append(f"soil={data['soil_moisture']:.0f}%")
                if data.get("light") is not None:
                    parts.append(f"light={data['light']}lux")

                print(f"  ✅ {sensor.name}: {', '.join(parts)}")
                total_readings += 1

            except Exception as e:
                print(f"  ❌ {sensor.name}: {e}")

    return total_readings


def main():
    parser = argparse.ArgumentParser(description="Sensor data logger")
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Run continuously with this interval in minutes (0 = run once)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Database path (default: ~/.openclaw/workspace/skills/tuya-irrigation/data/irrigation.db)",
    )
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else None
    db = IrrigationDB(db_path)
    device_manager = TuyaDeviceManager()

    if args.interval <= 0:
        # Run once
        readings = log_sensor_readings(db, device_manager)
        print(f"\n📊 Logged {readings} sensor reading(s)")
        db.close()
        return 0

    # Continuous mode
    print(f"🔄 Starting continuous logging (every {args.interval} minutes)")
    print("   Press Ctrl+C to stop")

    try:
        while True:
            readings = log_sensor_readings(db, device_manager)
            print(f"\n📊 Logged {readings} sensor reading(s)")
            print(f"   Next run in {args.interval} minutes...\n")
            time.sleep(args.interval * 60)
    except KeyboardInterrupt:
        print("\n⏹️  Stopped by user")
        db.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
