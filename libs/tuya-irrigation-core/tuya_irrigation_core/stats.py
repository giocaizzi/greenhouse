"""Irrigation statistics and reporting."""

import time
from collections import defaultdict

from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_core.utils import format_timestamp


def format_duration(minutes: int) -> str:
    """Format duration in human-readable format."""
    if minutes < 60:
        return f"{minutes}min"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}min" if mins else f"{hours}h"


def get_irrigation_stats(db: IrrigationRepository, cluster_id: int, days: int = 7) -> dict:
    """Get irrigation statistics for a cluster."""
    cutoff = int(time.time()) - (days * 24 * 3600)

    irrigators = db.get_irrigators_in_cluster(cluster_id)
    if not irrigators:
        return {"error": "No irrigators in cluster"}

    stats = {
        "period_days": days,
        "total_events": 0,
        "total_duration_minutes": 0,
        "events_by_type": defaultdict(int),
        "events_by_trigger": defaultdict(int),
        "irrigations": [],
        "avg_duration_minutes": 0,
        "frequency_per_day": 0,
    }

    for irrigator in irrigators:
        events = db.get_recent_events(irrigator.id, hours=days * 24)

        for event in events:
            if event.timestamp < cutoff:
                continue

            stats["total_events"] += 1
            stats["events_by_type"][event.action] += 1
            stats["events_by_trigger"][event.triggered_by] += 1

            if event.duration_minutes:
                stats["total_duration_minutes"] += event.duration_minutes
                stats["irrigations"].append(
                    {
                        "timestamp": event.timestamp,
                        "duration_minutes": event.duration_minutes,
                        "triggered_by": event.triggered_by,
                        "irrigator": irrigator.name,
                    }
                )

    # Calculate averages
    if stats["irrigations"]:
        stats["avg_duration_minutes"] = stats["total_duration_minutes"] / len(stats["irrigations"])
        stats["frequency_per_day"] = len(stats["irrigations"]) / days

    return stats


def print_stats_report(stats: dict, cluster_name: str):
    """Print formatted statistics report."""
    if "error" in stats:
        print(f"❌ {stats['error']}")
        return

    print(f"\n📊 Irrigation Statistics - {cluster_name}")
    print(f"   Period: last {stats['period_days']} days")
    print("\n🔢 Summary:")
    print(f"   Total events: {stats['total_events']}")
    print(f"   Irrigations: {len(stats['irrigations'])}")
    print(f"   Total water time: {format_duration(stats['total_duration_minutes'])}")

    if stats["irrigations"]:
        print(f"   Average per irrigation: {format_duration(int(stats['avg_duration_minutes']))}")
        print(f"   Frequency: {stats['frequency_per_day']:.1f} times/day")

    if stats["events_by_type"]:
        print("\n📋 Events by type:")
        for event_type, count in sorted(stats["events_by_type"].items()):
            print(f"   {event_type}: {count}")

    if stats["events_by_trigger"]:
        print("\n🎯 Triggered by:")
        for trigger, count in sorted(stats["events_by_trigger"].items()):
            print(f"   {trigger}: {count}")

    if stats["irrigations"]:
        print("\n💧 Recent irrigations:")
        for irr in stats["irrigations"][-5:]:  # Last 5
            ts = format_timestamp(irr["timestamp"])
            print(f"   {ts} | {format_duration(irr['duration_minutes'])} | {irr['triggered_by']} | {irr['irrigator']}")


def export_csv(db: IrrigationRepository, cluster_id: int, days: int, output_path: str):
    """Export irrigation events to CSV."""
    import csv

    cutoff = int(time.time()) - (days * 24 * 3600)
    irrigators = db.get_irrigators_in_cluster(cluster_id)

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["timestamp", "date", "time", "irrigator", "action", "duration_minutes", "triggered_by", "notes"]
        )

        for irrigator in irrigators:
            events = db.get_recent_events(irrigator.id, hours=days * 24)
            for event in events:
                if event.timestamp < cutoff:
                    continue
                date_str = format_timestamp(event.timestamp, fmt="%Y-%m-%d")
                time_str = format_timestamp(event.timestamp, fmt="%H:%M:%S")
                writer.writerow(
                    [
                        event.timestamp,
                        date_str,
                        time_str,
                        irrigator.name,
                        event.action,
                        event.duration_minutes or "",
                        event.triggered_by,
                        event.notes or "",
                    ]
                )

    print(f"✅ Exported to {output_path}")
