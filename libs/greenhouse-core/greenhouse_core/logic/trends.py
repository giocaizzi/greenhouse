"""Historical trend analysis for irrigation decisions."""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, cast

from greenhouse_core.constants import (
    CADENCE_HIGH_EVENTS_PER_DAY,
    CADENCE_LOW_AVG_MINUTES,
    CADENCE_LOW_EVENTS_PER_DAY,
    CADENCE_WINDOW_DAYS,
    TREND_LOOKBACK_HOURS,
    TREND_MIN_READINGS,
    TREND_MOISTURE_THRESHOLD,
    TREND_TEMP_THRESHOLD,
)
from greenhouse_core.logic.cleaning import clean_readings
from greenhouse_core.logic.decision import Trends
from greenhouse_core.repository import IrrigationRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    from greenhouse_core.logic.cleaning import CleanedReading
    from greenhouse_core.models import IrrigationEvent, Sensor


def _pooled_clean_readings(db: IrrigationRepository, sensors: Sequence[Sensor], *, hours: int) -> list[CleanedReading]:
    """Clean each sensor's series before pooling.

    The Hampel filter is only valid within one sensor's own timeline, not across sensors.
    """
    pooled: list[CleanedReading] = []
    for sensor in sensors:
        pooled.extend(clean_readings(db.get_recent_readings(sensor.id, hours=hours)))
    return pooled


def _moisture_trend(first: Sequence[CleanedReading], second: Sequence[CleanedReading]) -> tuple[float, str] | None:
    """Delta and label of the soil-moisture means between the two halves, or None without data."""
    # `is not None`, not truthiness: a genuine 0.0% reading (bone-dry or
    # disconnected probe) must count, not be silently dropped.
    moisture_first = [r.soil_moisture for r in first if r.soil_moisture is not None]
    moisture_second = [r.soil_moisture for r in second if r.soil_moisture is not None]
    if not moisture_first or not moisture_second:
        return None
    delta = statistics.mean(moisture_second) - statistics.mean(moisture_first)
    if delta < -TREND_MOISTURE_THRESHOLD:
        return delta, "declining"
    if delta > TREND_MOISTURE_THRESHOLD:
        return delta, "rising"
    return delta, "stable"


def _temperature_trend(first: Sequence[CleanedReading], second: Sequence[CleanedReading]) -> str | None:
    """Label of the temperature means between the two halves, or None without data."""
    # Truthiness filter kept on purpose: a 0 °C reading is dropped (B-18, preserved).
    temp_first = [r.temperature for r in first if r.temperature]
    temp_second = [r.temperature for r in second if r.temperature]
    if not temp_first or not temp_second:
        return None
    delta_temp = statistics.mean(temp_second) - statistics.mean(temp_first)
    if delta_temp > TREND_TEMP_THRESHOLD:
        return "rising"
    if delta_temp < -TREND_TEMP_THRESHOLD:
        return "falling"
    return "stable"


def _apply_reading_trends(trends: Trends, readings: list[CleanedReading]) -> None:
    """Compare the older and newer halves of the pooled window."""
    readings.sort(key=lambda r: r.timestamp)
    mid = len(readings) // 2
    first, second = readings[:mid], readings[mid:]
    moisture = _moisture_trend(first, second)
    if moisture is not None:
        trends.soil_moisture_delta, trends.soil_moisture_trend = moisture
    temperature = _temperature_trend(first, second)
    if temperature is not None:
        trends.temperature_trend = temperature


def _apply_cadence_flags(trends: Trends, events: Sequence[IrrigationEvent]) -> None:
    """Flag a too-sparse or too-frequent irrigation cadence over the window."""
    # Only real actuation (`start`) counts as irrigation. `schedule_updated`
    # is a config change, not water, so it must not inflate the cadence.
    irrigation_events = [e for e in events if e.action == "start" and e.duration_minutes]
    total_events = len(irrigation_events)
    if total_events <= 0:
        return
    total_duration = sum(cast(int, e.duration_minutes) for e in irrigation_events)
    avg_per_day = total_events / CADENCE_WINDOW_DAYS
    avg_duration = total_duration / total_events
    if avg_per_day < CADENCE_LOW_EVENTS_PER_DAY and avg_duration < CADENCE_LOW_AVG_MINUTES:
        trends.irrigation_frequency_low = True
    elif avg_per_day > CADENCE_HIGH_EVENTS_PER_DAY:
        trends.irrigation_frequency_high = True


def analyze_historical_trends(db: IrrigationRepository, cluster_id: int) -> Trends:
    """Detect 48h moisture/temperature trends and 7d irrigation cadence."""
    trends = Trends()

    sensors = db.get_sensors_in_cluster(cluster_id)
    readings = _pooled_clean_readings(db, sensors, hours=TREND_LOOKBACK_HOURS)
    if len(readings) >= TREND_MIN_READINGS:
        _apply_reading_trends(trends, readings)

    irrigator = db.get_irrigator_for_cluster(cluster_id)
    if irrigator is not None:
        events = db.get_recent_events(irrigator.id, hours=CADENCE_WINDOW_DAYS * 24)
        _apply_cadence_flags(trends, events)

    return trends
