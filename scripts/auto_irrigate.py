#!/usr/bin/env python3
"""HEARTBEAT entrypoint for smart irrigation.

Data flow:
1. Sync sensor data from Tuya Cloud → local DB (backfill + live)
2. Read live sensor values for decision (soil moisture + temperature)
3. Fallback to Open-Meteo temperature if no sensors available
4. Run smart irrigation logic
5. Execute on physical device if needed

Usage:
    python3 scripts/auto_irrigate.py
    python3 scripts/auto_irrigate.py --temp 15.0   # override temp
"""

import argparse
import json
import sys
import urllib.request

# Initialize path for package imports
import _init_path  # noqa: F401

from tuya_irrigation.db import IrrigationDB  # noqa: E402
from tuya_irrigation.logic import IrrigationLogic  # noqa: E402

# Milano coordinates
MILANO_LAT = 45.464
MILANO_LON = 9.189


def fetch_feels_like() -> float | None:
    """Fetch current feels-like temperature for Milano from Open-Meteo (no API key)."""
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={MILANO_LAT}&longitude={MILANO_LON}"
        f"&current=apparent_temperature"
        f"&timezone=Europe/Rome"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())
            return float(data["current"]["apparent_temperature"])
    except Exception as e:
        print(f"⚠️  Open-Meteo fetch failed: {e}", file=sys.stderr)
        return None


def sync_sensors(db: IrrigationDB, cluster_id: int) -> dict | None:
    """Sync sensor data from Tuya Cloud and return live reading.

    Returns dict with sensor values or None if no sensors/cloud unavailable.
    """
    sensors = db.get_sensors_in_cluster(cluster_id)
    if not sensors:
        return None

    try:
        from tuya_irrigation.cloud import TuyaCloud  # noqa: E402

        cloud = TuyaCloud()

        # Sync historical logs to DB (backfill any gaps)
        for sensor in sensors:
            try:
                from tuya_irrigation.logger_daemon import _sync_single_sensor

                _sync_single_sensor(db, cloud, sensor, hours=6)
            except Exception:
                pass

        # Get live reading from first sensor
        live = cloud.get_live_reading(sensors[0].tuya_device_id)
        return live if live else None

    except Exception as e:
        print(f"⚠️  Cloud sync failed: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="HEARTBEAT irrigation entrypoint")
    parser.add_argument("--temp", type=float, help="Current temperature in °C (overrides all)")
    parser.add_argument("--cluster", type=int, default=1, help="Cluster ID (default: 1)")
    parser.add_argument("--db", help="Database path (default: auto)")
    args = parser.parse_args()

    from pathlib import Path

    db_path = Path(args.db) if args.db else None
    db = IrrigationDB(db_path)
    logic = IrrigationLogic(db)

    # Step 1: Sync sensor data from Tuya Cloud
    sensor_data = None
    if args.temp is None:
        sensor_data = sync_sensors(db, args.cluster)

    # Step 2: Determine temperature
    temp = args.temp
    if temp is None and sensor_data and sensor_data.get("temperature"):
        temp = sensor_data["temperature"]
    if temp is None:
        temp = fetch_feels_like()
    if temp is None:
        print("⚠️  No temperature available — using seasonal fallback (20°C)", file=sys.stderr)
        temp = 20.0

    # Step 3: Run decision logic
    # Note: logic.decide_for_cluster reads from DB, which now has synced sensor data
    decision = logic.decide_for_cluster(args.cluster, current_temp=temp)
    if decision is None:
        print(f"❌ Cluster {args.cluster} not found", file=sys.stderr)
        return 1

    action = decision.get("action", "skip")
    reason = decision.get("reason", "")
    confidence = decision.get("confidence", 0)
    duration = decision.get("duration_minutes", 2)

    if action == "irrigate":
        # Build info line
        info_parts = [f"Reason: {reason}", f"Temp: {temp}°C"]
        if sensor_data:
            if sensor_data.get("soil_moisture") is not None:
                info_parts.append(f"Soil: {sensor_data['soil_moisture']:.0f}%")
            info_parts.append("Source: sensor")
        else:
            info_parts.append("Source: Open-Meteo")

        print(f"💧 Irrigating cluster {args.cluster}: {duration}min (confidence: {confidence:.0%})")
        for part in info_parts:
            print(f"   {part}")

        # Get irrigator
        irrigators = db.get_irrigators_in_cluster(args.cluster)
        if not irrigators:
            print(f"❌ No irrigators found in cluster {args.cluster}", file=sys.stderr)
            return 1

        irrigator = irrigators[0]

        # Execute irrigation
        device_success = False
        device_error = None

        try:
            from tuya_irrigation.devices import TuyaDeviceManager  # noqa: E402

            manager = TuyaDeviceManager()
            device_success, output = manager.irrigator_start(irrigator, minutes=duration)
            if not device_success:
                device_error = output
        except Exception as e:
            device_error = str(e)

        # Always log the decision
        soil_info = f", soil={sensor_data['soil_moisture']:.0f}%" if sensor_data and sensor_data.get("soil_moisture") is not None else ""
        db.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="start" if device_success else "attempted",
            duration_minutes=duration,
            triggered_by="auto_heartbeat",
            notes=f"temp={temp}°C{soil_info}, confidence={confidence:.0%}, reason={reason}, device_success={device_success}",
        )

        if device_success:
            print("✅ Irrigation started successfully")
        else:
            print(f"⚠️  Decision logged but device execution failed: {device_error}", file=sys.stderr)
    elif action == "skip":
        # Silent on skip — heartbeat shouldn't be noisy
        pass
    else:
        print(f"ℹ️  Action: {action} — {reason}")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
