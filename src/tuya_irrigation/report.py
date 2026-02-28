#!/usr/bin/env python3
"""Periodic irrigation report generator."""

import argparse
import sys
import time
from pathlib import Path

from tuya_irrigation.db import IrrigationDB
from tuya_irrigation.stats import get_irrigation_stats
from tuya_irrigation.utils import format_timestamp


def generate_report(db: IrrigationDB, cluster_id: int, days: int) -> str:
    """Generate a formatted text report."""
    cluster = db.get_cluster(cluster_id)
    if not cluster:
        return f"❌ Cluster {cluster_id} not found"

    stats = get_irrigation_stats(db, cluster_id, days)
    if "error" in stats:
        return f"❌ {stats['error']}"

    # Build report
    lines = []
    lines.append(f"🌱 Irrigation Report: {cluster.name}")
    lines.append(f"📅 Period: {format_timestamp(time.time(), fmt='%Y-%m-%d')} (last {days} days)")
    lines.append("")

    if stats["irrigations"]:
        lines.append("💧 Summary:")
        lines.append(f"• Irrigations: {len(stats['irrigations'])}")
        lines.append(f"• Total water time: {stats['total_duration_minutes']}min")
        lines.append(f"• Average per irrigation: {int(stats['avg_duration_minutes'])}min")
        lines.append(f"• Frequency: {stats['frequency_per_day']:.1f} times/day")
        lines.append("")

        # Trigger breakdown
        lines.append("🎯 Triggered by:")
        for trigger, count in sorted(stats["events_by_trigger"].items()):
            pct = (count / stats["total_events"]) * 100
            lines.append(f"• {trigger}: {count} ({pct:.0f}%)")
        lines.append("")

        # Recent activity
        lines.append("📋 Recent irrigations:")
        for irr in stats["irrigations"][-5:]:
            ts = format_timestamp(irr["timestamp"], fmt="%m/%d %H:%M")
            lines.append(f"• {ts}: {irr['duration_minutes']}min ({irr['triggered_by']})")
    else:
        lines.append("ℹ️ No irrigations in this period")

    # Plant info
    plants = db.get_plants_in_cluster(cluster_id)
    if plants:
        lines.append("")
        lines.append(f"🌿 Plants: {len(plants)}")
        for p in plants:
            water = f" [{p.water_needs}]" if p.water_needs else ""
            lines.append(f"• {p.species}{water}")

    # Current config
    config = db.get_irrigation_config(cluster_id)
    if config:
        lines.append("")
        lines.append("⚙️ Configuration:")
        lines.append(f"• Mode: {config.mode}")
        lines.append(f"• Schedule: {config.duration_minutes}min every {config.interval_hours}h")
        lines.append(f"• Auto-run: {'ON' if config.auto_run else 'OFF'}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate periodic irrigation report")
    parser.add_argument("cluster", type=int, help="Cluster ID")
    parser.add_argument("--days", type=int, default=7, help="Number of days to report")
    parser.add_argument("--output", help="Write report to file (default: stdout)")
    parser.add_argument("--db", help="Database path (default: auto)")

    args = parser.parse_args()

    db_path = Path(args.db) if args.db else None
    db = IrrigationDB(db_path)

    try:
        report = generate_report(db, args.cluster, args.days)

        if args.output:
            Path(args.output).write_text(report, encoding="utf-8")
            print(f"✅ Report written to {args.output}")
        else:
            print(report)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
