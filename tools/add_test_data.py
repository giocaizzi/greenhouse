#!/usr/bin/env python3
"""Add fake irrigation events for testing reports."""

import sys
import time
from pathlib import Path

# Add src to path for package imports
skill_root = Path(__file__).parent.parent
sys.path.insert(0, str(skill_root / "src"))

from tuya_irrigation.db import IrrigationDB  # noqa: E402


def add_test_data(db: IrrigationDB, cluster_id: int, days: int = 7):
    """Add fake irrigation events for testing."""
    irrigators = db.get_irrigators_in_cluster(cluster_id)
    if not irrigators:
        print("❌ No irrigators in cluster")
        return

    irrigator = irrigators[0]
    now = int(time.time())

    # Simulate irrigation events over past N days
    events_added = 0

    for day in range(days):
        # 2 irrigations per day
        for session in range(2):
            timestamp = now - ((days - day) * 24 * 3600) + (session * 12 * 3600)

            # Alternate between auto and manual triggers
            triggered_by = "auto" if session % 2 == 0 else "manual"
            duration = 2 if triggered_by == "auto" else 3

            db.add_irrigation_event(
                irrigator_id=irrigator.id,
                action="start",
                duration_minutes=duration,
                triggered_by=triggered_by,
                timestamp=timestamp,
                notes=f"Test event (day {day+1}, session {session+1})",
            )
            events_added += 1

    print(f"✅ Added {events_added} test irrigation events")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Add test irrigation data")
    parser.add_argument("cluster", type=int, help="Cluster ID")
    parser.add_argument("--days", type=int, default=7, help="Days of test data")
    parser.add_argument("--db", help="Database path (default: auto)")

    args = parser.parse_args()

    db_path = Path(args.db) if args.db else None
    db = IrrigationDB(db_path)

    try:
        add_test_data(db, args.cluster, args.days)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
