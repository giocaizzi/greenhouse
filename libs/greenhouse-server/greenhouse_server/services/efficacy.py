"""Irrigation efficacy scorer: post-hoc moisture-rise analysis per event."""

import time
from collections.abc import Sequence
from statistics import mean
from typing import TYPE_CHECKING

from greenhouse_core.constants import EFFICACY_AFTER_WINDOW_SECONDS, RESPONSE_PRE_WINDOW_SECONDS, SECONDS_PER_DAY
from greenhouse_core.logic.cleaning import clean_readings_around
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.schemas import EfficacyItemResponse, EfficacyListResponse

if TYPE_CHECKING:
    from greenhouse_core.models import IrrigationEvent, Irrigator, Sensor

# Look-back bounds for ``days``: one rule for the JSON API and the web page (D17).
EFFICACY_DEFAULT_DAYS = 14
EFFICACY_MAX_DAYS = 365


def _score(before_pct: float | None, after_pct: float | None) -> float | None:
    if before_pct is None or after_pct is None:
        return None
    rise = after_pct - before_pct
    return max(0.0, min(100.0, rise * 5.0))


def score_cluster(
    repo: IrrigationRepository, cluster_id: int, days: int = EFFICACY_DEFAULT_DAYS
) -> EfficacyListResponse:
    """Score completed irrigation events for a cluster over the given window.

    Args:
        cluster_id: Cluster to analyse.
        days: Look-back window in days (default 14).

    Returns:
        EfficacyListResponse with one scored item per qualifying event, newest-first.
    """
    cutoff = int(time.time()) - days * SECONDS_PER_DAY
    irrigator = repo.get_irrigator_for_cluster(cluster_id)
    sensors = repo.get_sensors_in_cluster(cluster_id)

    items: list[EfficacyItemResponse] = []

    if irrigator is not None:
        events = repo.list_start_events_since(irrigator.id, cutoff)

        for event in events:
            item = _event_item(repo, event, sensors, irrigator)
            if item is not None:
                items.append(item)

    items.sort(key=lambda x: x.timestamp, reverse=True)
    return EfficacyListResponse(cluster_id=cluster_id, days=days, items=items)


def _event_item(
    repo: IrrigationRepository, event: "IrrigationEvent", sensors: "Sequence[Sensor]", irrigator: "Irrigator"
) -> EfficacyItemResponse | None:
    """Score one start event by the cluster's moisture rise around it; None for an event without a duration."""
    duration = event.duration_minutes
    if not duration or duration <= 0:
        return None

    before_vals: list[float] = []
    after_vals: list[float] = []
    for sensor in sensors:
        before_rows, after_rows = repo.get_readings_around(
            sensor.id,
            event.timestamp,
            before_seconds=RESPONSE_PRE_WINDOW_SECONDS,
            after_seconds=EFFICACY_AFTER_WINDOW_SECONDS,
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
