#!/usr/bin/env python3
"""HEARTBEAT entrypoint for smart irrigation.

Fetches feels-like temperature from Open-Meteo (no API key required),
then runs auto-irrigate logic for the indoor cluster.

Usage:
    python3 scripts/auto_irrigate.py
    python3 scripts/auto_irrigate.py --temp 15.0   # override temp
"""

import argparse
import sys
import urllib.request
import json

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


def main():
    parser = argparse.ArgumentParser(description="HEARTBEAT irrigation entrypoint")
    parser.add_argument("--temp", type=float, help="Current temperature in °C (overrides Open-Meteo fetch)")
    parser.add_argument("--cluster", type=int, default=1, help="Cluster ID (default: 1)")
    parser.add_argument("--db", help="Database path (default: auto)")
    args = parser.parse_args()

    # Determine temperature
    temp = args.temp
    if temp is None:
        temp = fetch_feels_like()
    if temp is None:
        print("⚠️  No temperature available — using seasonal fallback (20°C)", file=sys.stderr)
        temp = 20.0

    # Run auto-irrigate
    from pathlib import Path
    db_path = Path(args.db) if args.db else None
    db = IrrigationDB(db_path)
    logic = IrrigationLogic(db)

    decision = logic.decide_for_cluster(args.cluster, current_temp=temp)
    if decision is None:
        print(f"❌ Cluster {args.cluster} not found", file=sys.stderr)
        return 1

    action = decision.get("action", "skip")
    reason = decision.get("reason", "")
    confidence = decision.get("confidence", 0)
    duration = decision.get("duration_minutes", 2)

    if action == "irrigate":
        print(f"💧 Irrigating cluster {args.cluster}: {duration}min (confidence: {confidence:.0%})")
        print(f"   Reason: {reason}")
        print(f"   Temp: {temp}°C")

        # Get irrigator
        irrigators = db.get_irrigators_in_cluster(args.cluster)
        if not irrigators:
            print(f"❌ No irrigators found in cluster {args.cluster}", file=sys.stderr)
            return 1

        irrigator = irrigators[0]

        # Try to execute irrigation (log decision even if device control fails)
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

        # Always log the decision (even if device execution failed)
        db.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="start" if device_success else "attempted",
            duration_minutes=duration,
            triggered_by="auto_heartbeat",
            notes=f"temp={temp}°C, confidence={confidence:.0%}, reason={reason}, device_success={device_success}",
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
