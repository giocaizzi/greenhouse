"""Irrigation statistics for a cluster."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING, TypedDict

from greenhouse_core.constants import SECONDS_PER_DAY
from greenhouse_core.repository import IrrigationRepository

if TYPE_CHECKING:
    from greenhouse_core.models import IrrigationEvent


class IrrigationRecord(TypedDict):
    """One in-window event that actually ran water (``duration_minutes`` set)."""

    timestamp: int
    duration_minutes: int
    triggered_by: str
    irrigator: str


class IrrigationStats(TypedDict):
    """Aggregates for one cluster's irrigator; key order is the output contract."""

    period_days: int
    total_events: int
    total_duration_minutes: int
    events_by_type: defaultdict[str, int]
    events_by_trigger: defaultdict[str, int]
    irrigations: list[IrrigationRecord]
    avg_duration_minutes: float
    frequency_per_day: float


class StatsUnavailable(TypedDict):
    """Returned instead of stats when the cluster has no irrigator."""

    error: str


def format_duration(minutes: int) -> str:
    """Format duration in human-readable format."""
    if minutes < 60:
        return f"{minutes}min"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}min" if mins else f"{hours}h"


def _empty_stats(days: int) -> IrrigationStats:
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


def _count_event(stats: IrrigationStats, event: IrrigationEvent, irrigator_name: str) -> None:
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


def get_irrigation_stats(
    db: IrrigationRepository, cluster_id: int, days: int = 7
) -> IrrigationStats | StatsUnavailable:
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
