"""Historical trend analysis for irrigation decisions."""

import statistics

from greenhouse_core.constants import TREND_MIN_READINGS, TREND_MOISTURE_THRESHOLD, TREND_TEMP_THRESHOLD
from greenhouse_core.logic.decision import Trends
from greenhouse_core.repository import IrrigationRepository


def analyze_historical_trends(db: IrrigationRepository, cluster_id: int) -> Trends:
    """Detect 48h moisture/temperature trends and 7d irrigation cadence."""
    trends = Trends()

    sensors = db.get_sensors_in_cluster(cluster_id)
    if sensors:
        all_readings = []
        for sensor in sensors:
            all_readings.extend(db.get_recent_readings(sensor.id, hours=48))

        if len(all_readings) >= TREND_MIN_READINGS:
            all_readings.sort(key=lambda r: r.timestamp)
            mid = len(all_readings) // 2

            moisture_first = [r.soil_moisture for r in all_readings[:mid] if r.soil_moisture]
            moisture_second = [r.soil_moisture for r in all_readings[mid:] if r.soil_moisture]
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
        events = db.get_recent_events(irrigator.id, hours=7 * 24)
        irrigation_events = [e for e in events if e.action in ("start", "schedule_updated") and e.duration_minutes]
        total_events = len(irrigation_events)
        total_duration = sum(e.duration_minutes for e in irrigation_events)

        if total_events > 0:
            avg_per_day = total_events / 7
            avg_duration = total_duration / total_events
            if avg_per_day < 1 and avg_duration < 2:
                trends.irrigation_frequency_low = True
            elif avg_per_day > 3:
                trends.irrigation_frequency_high = True

    return trends
