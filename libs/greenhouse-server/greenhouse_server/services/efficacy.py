"""Irrigation efficacy scorer: post-hoc moisture-rise analysis per event."""

import time
from collections.abc import Sequence
from statistics import mean
from typing import TYPE_CHECKING

from sqlalchemy import select

from greenhouse_core.logic.cleaning import clean_readings_around
from greenhouse_core.models import IrrigationEvent
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.schemas import EfficacyItemResponse, EfficacyListResponse

if TYPE_CHECKING:
    from greenhouse_core.models import Irrigator, Sensor

_BEFORE_SECONDS = 1800
_AFTER_SECONDS = 5400


def _score(before_pct: float | None, after_pct: float | None) -> float | None:
    if before_pct is None or after_pct is None:
        return None
    rise = after_pct - before_pct
    return max(0.0, min(100.0, rise * 5.0))


def score_cluster(repo: IrrigationRepository, cluster_id: int, days: int = 14) -> EfficacyListResponse:
    """Score completed irrigation events for a cluster over the given window.

    Args:
        cluster_id: Cluster to analyse.
        days: Look-back window in days (default 14).

    Returns:
        EfficacyListResponse with one scored item per qualifying event, newest-first.
    """
    cutoff = int(time.time()) - days * 86400
    irrigator = repo.get_irrigator_for_cluster(cluster_id)
    sensors = repo.get_sensors_in_cluster(cluster_id)

    items: list[EfficacyItemResponse] = []

    if irrigator is not None:
        events = list(
            repo.session.scalars(
                select(IrrigationEvent)
                .where(
                    IrrigationEvent.irrigator_id == irrigator.id,
                    IrrigationEvent.action == "start",
                    IrrigationEvent.timestamp >= cutoff,
                )
                .order_by(IrrigationEvent.timestamp.desc())
            )
        )

        for event in events:
            item = _event_item(repo, event, sensors, irrigator)
            if item is not None:
                items.append(item)

    items.sort(key=lambda x: x.timestamp, reverse=True)
    return EfficacyListResponse(cluster_id=cluster_id, days=days, items=items)


def _event_item(
    repo: IrrigationRepository, event: IrrigationEvent, sensors: "Sequence[Sensor]", irrigator: "Irrigator"
) -> EfficacyItemResponse | None:
    """Score one start event by the cluster's moisture rise around it; None for an event without a duration."""
    duration = event.duration_minutes
    if not duration or duration <= 0:
        return None

    before_vals: list[float] = []
    after_vals: list[float] = []
    for sensor in sensors:
        before_rows, after_rows = repo.get_readings_around(
            sensor.id, event.timestamp, before_seconds=_BEFORE_SECONDS, after_seconds=_AFTER_SECONDS
        )
        # Cleaned view: the score is rise × 5, so one spike reading is
        # worth up to 100 points of phantom efficacy.
        before_readings, after_readings = clean_readings_around(before_rows, after_rows)
        before_vals.extend(r.soil_moisture for r in before_readings if r.soil_moisture is not None)
        after_vals.extend(r.soil_moisture for r in after_readings if r.soil_moisture is not None)

    before_pct = mean(before_vals) if before_vals else None
    after_pct = mean(after_vals) if after_vals else None

    return EfficacyItemResponse(
        event_id=event.id,
        timestamp=event.timestamp,
        irrigator_name=irrigator.name,
        duration_minutes=duration,
        before_pct=before_pct,
        after_pct=after_pct,
        score=_score(before_pct, after_pct),
    )
