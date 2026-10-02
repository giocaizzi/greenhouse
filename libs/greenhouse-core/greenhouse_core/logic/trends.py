"""Historical trend analysis for irrigation decisions."""

import statistics
from typing import cast

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


def analyze_historical_trends(db: IrrigationRepository, cluster_id: int) -> Trends:
    """Detect 48h moisture/temperature trends and 7d irrigation cadence."""
    trends = Trends()

    sensors = db.get_sensors_in_cluster(cluster_id)
    if sensors:
        all_readings = []
        for sensor in sensors:
            # Clean each sensor's series before pooling — the Hampel filter is
            # only valid within one sensor's own timeline, not across sensors.
            all_readings.extend(clean_readings(db.get_recent_readings(sensor.id, hours=TREND_LOOKBACK_HOURS)))

        if len(all_readings) >= TREND_MIN_READINGS:
            all_readings.sort(key=lambda r: r.timestamp)
            mid = len(all_readings) // 2

            # `is not None`, not truthiness: a genuine 0.0% reading (bone-dry or
            # disconnected probe) must count, not be silently dropped.
            moisture_first = [r.soil_moisture for r in all_readings[:mid] if r.soil_moisture is not None]
            moisture_second = [r.soil_moisture for r in all_readings[mid:] if r.soil_moisture is not None]
            if moisture_first and moisture_second:
                delta = statistics.mean(moisture_second) - statistics.mean(moisture_first)
                trends.soil_moisture_delta = delta
                if delta < -TREND_MOISTURE_THRESHOLD:
                    trends.soil_moisture_trend = "declining"
                elif delta > TREND_MOISTURE_THRESHOLD:
                    trends.soil_moisture_trend = "rising"
                else:
                    trends.soil_moisture_trend = "stable"

            temp_first = [r.temperature for r in all_readings[:mid] if r.temperature]
            temp_second = [r.temperature for r in all_readings[mid:] if r.temperature]
            if temp_first and temp_second:
                delta_temp = statistics.mean(temp_second) - statistics.mean(temp_first)
                if delta_temp > TREND_TEMP_THRESHOLD:
                    trends.temperature_trend = "rising"
                elif delta_temp < -TREND_TEMP_THRESHOLD:
                    trends.temperature_trend = "falling"
                else:
                    trends.temperature_trend = "stable"

    irrigator = db.get_irrigator_for_cluster(cluster_id)
    if irrigator is not None:
        events = db.get_recent_events(irrigator.id, hours=CADENCE_WINDOW_DAYS * 24)
        # Only real actuation (`start`) counts as irrigation. `schedule_updated`
        # is a config change, not water, so it must not inflate the cadence.
        irrigation_events = [e for e in events if e.action == "start" and e.duration_minutes]
        total_events = len(irrigation_events)
        total_duration = sum(cast(int, e.duration_minutes) for e in irrigation_events)

        if total_events > 0:
            avg_per_day = total_events / CADENCE_WINDOW_DAYS
            avg_duration = total_duration / total_events
            if avg_per_day < CADENCE_LOW_EVENTS_PER_DAY and avg_duration < CADENCE_LOW_AVG_MINUTES:
                trends.irrigation_frequency_low = True
            elif avg_per_day > CADENCE_HIGH_EVENTS_PER_DAY:
                trends.irrigation_frequency_high = True

    return trends
