#!/usr/bin/env python3
"""HEARTBEAT entrypoint for smart irrigation.

Parses WTTR weather string to extract feels-like temperature,
then runs auto-irrigate logic for the indoor cluster.

Usage (from HEARTBEAT.md):
    python3 scripts/auto_irrigate.py --wttr "$WTTR"
"""

import argparse
import re
import sys
from pathlib import Path

# Add src to path for package imports
skill_root = Path(__file__).parent.parent
sys.path.insert(0, str(skill_root / "src"))  # noqa: E402

from tuya_irrigation.db import IrrigationDB  # noqa: E402
from tuya_irrigation.logic import IrrigationLogic  # noqa: E402


def parse_feels_like(wttr: str) -> float | None:
    """Extract feels-like temperature from wttr format string.

    Example: 'Milano: ⛅ +12°C (+8°C), pioggia 0.0mm, vento 15km/h'
    Returns the feels-like value in parentheses (8 in the example above).
    """
    # Match feels-like in parentheses: (+8°C) or (-2°C) or (8°C)
    match = re.search(r"\(([+-]?\d+)°C\)", wttr)
    if match:
        return float(match.group(1))
    # Fallback: match first temperature
    match = re.search(r"([+-]?\d+)°C", wttr)
    if match:
        return float(match.group(1))
    return None


def main():
    parser = argparse.ArgumentParser(description="HEARTBEAT irrigation entrypoint")
    parser.add_argument("--wttr", help="WTTR weather string (to extract feels-like temp)")
    parser.add_argument("--temp", type=float, help="Current temperature in °C (overrides --wttr)")
    parser.add_argument("--cluster", type=int, default=1, help="Cluster ID (default: 1)")
    parser.add_argument("--db", help="Database path (default: auto)")
    args = parser.parse_args()

    # Determine temperature
    temp = args.temp
    if temp is None and args.wttr:
        temp = parse_feels_like(args.wttr)
    if temp is None:
        print("⚠️  No temperature available — using seasonal fallback (20°C)", file=sys.stderr)
        temp = 20.0

    # Run auto-irrigate
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
            # Don't return error — we still logged the decision
    elif action == "skip":
        # Silent on skip — heartbeat shouldn't be noisy
        pass
    else:
        print(f"ℹ️  Action: {action} — {reason}")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
