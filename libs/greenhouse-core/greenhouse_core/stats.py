"""Irrigation statistics for a cluster (and the legacy CSV export)."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from greenhouse_core.constants import SECONDS_PER_DAY
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.utils import format_timestamp

if TYPE_CHECKING:
    from _csv import Writer as _CsvWriter

    from greenhouse_core.models import IrrigationEvent, Irrigator


def format_duration(minutes: int) -> str:
    """Format duration in human-readable format."""
    if minutes < 60:
        return f"{minutes}min"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}min" if mins else f"{hours}h"


def _empty_stats(days: int) -> dict[str, Any]:
    """The stats dict before any event is counted (key order is the output contract)."""
    return {
        "period_days": days,
        "total_events": 0,
        "total_duration_minutes": 0,
        "events_by_type": defaultdict(int),
        "events_by_trigger": defaultdict(int),
        "irrigations": [],
        "avg_duration_minutes": 0,
        "frequency_per_day": 0,
    }


def _count_event(stats: dict[str, Any], event: IrrigationEvent, irrigator_name: str) -> None:
    """Add one in-window event to the running totals (and to the irrigation list if it ran)."""
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
                "irrigator": irrigator_name,
            }
        )


def get_irrigation_stats(db: IrrigationRepository, cluster_id: int, days: int = 7) -> dict[str, Any]:
    """Get irrigation statistics for a cluster."""
    cutoff = int(time.time()) - (days * SECONDS_PER_DAY)

    irrigator = db.get_irrigator_for_cluster(cluster_id)
    if irrigator is None:
        return {"error": "No irrigators in cluster"}

    stats = _empty_stats(days)
    events = db.get_recent_events(irrigator.id, hours=days * 24)

    for event in events:
        if event.timestamp < cutoff:
            continue
        _count_event(stats, event, irrigator.name)

    # Calculate averages
    if stats["irrigations"]:
        stats["avg_duration_minutes"] = stats["total_duration_minutes"] / len(stats["irrigations"])
        stats["frequency_per_day"] = len(stats["irrigations"]) / days

    return stats


def _csv_event_row(event: IrrigationEvent, irrigator: Irrigator) -> list[object]:
    """One CSV row: raw timestamp, local date and time, then the event fields."""
    date_str = format_timestamp(event.timestamp, fmt="%Y-%m-%d")
    time_str = format_timestamp(event.timestamp, fmt="%H:%M:%S")
    return [
        event.timestamp,
        date_str,
        time_str,
        irrigator.name,
        event.action,
        event.duration_minutes or "",
        event.triggered_by,
        event.notes or "",
    ]


def _write_event_rows(writer: _CsvWriter, events: list[IrrigationEvent], irrigator: Irrigator, cutoff: int) -> None:
    """Write the rows of the events inside the window, in repository order."""
    for event in events:
        if event.timestamp < cutoff:
            continue
        writer.writerow(_csv_event_row(event, irrigator))


def export_csv(db: IrrigationRepository, cluster_id: int, days: int, output_path: str) -> None:
    """Export irrigation events to CSV."""
    import csv

    cutoff = int(time.time()) - (days * SECONDS_PER_DAY)
    irrigator = db.get_irrigator_for_cluster(cluster_id)

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["timestamp", "date", "time", "irrigator", "action", "duration_minutes", "triggered_by", "notes"]
        )

        if irrigator is not None:
            _write_event_rows(writer, db.get_recent_events(irrigator.id, hours=days * 24), irrigator, cutoff)

    print(f"✅ Exported to {output_path}")
