"""Irrigation efficacy scorer: post-hoc moisture-rise analysis per event."""

import time
from statistics import mean

from sqlalchemy import select

from greenhouse_core.models import IrrigationEvent
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.schemas import EfficacyItemResponse, EfficacyListResponse

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
    irrigators = repo.get_irrigators_in_cluster(cluster_id)
    sensors = repo.get_sensors_in_cluster(cluster_id)

    items: list[EfficacyItemResponse] = []

    for irrigator in irrigators:
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
            duration = event.duration_minutes
            if not duration or duration <= 0:
                continue

            before_vals: list[float] = []
            after_vals: list[float] = []
            for sensor in sensors:
                before_readings, after_readings = repo.get_readings_around(
                    sensor.id, event.timestamp, before_seconds=_BEFORE_SECONDS, after_seconds=_AFTER_SECONDS
                )
                before_vals.extend(r.soil_moisture for r in before_readings if r.soil_moisture is not None)
                after_vals.extend(r.soil_moisture for r in after_readings if r.soil_moisture is not None)

            before_pct = mean(before_vals) if before_vals else None
            after_pct = mean(after_vals) if after_vals else None

            items.append(
                EfficacyItemResponse(
                    event_id=event.id,
                    timestamp=event.timestamp,
                    irrigator_name=irrigator.name,
                    duration_minutes=duration,
                    before_pct=before_pct,
                    after_pct=after_pct,
                    score=_score(before_pct, after_pct),
                )
            )

    items.sort(key=lambda x: x.timestamp, reverse=True)
    return EfficacyListResponse(cluster_id=cluster_id, days=days, items=items)
