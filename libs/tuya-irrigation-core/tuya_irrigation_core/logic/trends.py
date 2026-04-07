"""Historical trend analysis for irrigation decisions."""

import statistics

from tuya_irrigation_core.constants import TREND_MIN_READINGS, TREND_MOISTURE_THRESHOLD, TREND_TEMP_THRESHOLD
from tuya_irrigation_core.repository import IrrigationRepository


def analyze_historical_trends(db: IrrigationRepository, cluster_id: int) -> dict:
    """Analyze historical sensor and irrigation data to detect trends.

    Returns dict with:
    - soil_moisture_trend: "rising", "declining", "stable", or absent
    - temperature_trend: "rising", "falling", "stable", or absent
    - irrigation_frequency_low: bool (under-watering pattern)
    - irrigation_frequency_high: bool (over-watering pattern)
    """
    trends = {}

    # Get sensor readings for last 48h
    sensors = db.get_sensors_in_cluster(cluster_id)
    if sensors:
        # Collect soil moisture history
        all_readings = []
        for sensor in sensors:
            readings = db.get_recent_readings(sensor.id, hours=48)
            all_readings.extend(readings)

        if len(all_readings) >= TREND_MIN_READINGS:
            all_readings.sort(key=lambda r: r.timestamp)

            # Soil moisture trend (compare first half vs second half)
            mid = len(all_readings) // 2
            moisture_first_half = [r.soil_moisture for r in all_readings[:mid] if r.soil_moisture]
            moisture_second_half = [r.soil_moisture for r in all_readings[mid:] if r.soil_moisture]

            if moisture_first_half and moisture_second_half:
                avg_first = statistics.mean(moisture_first_half)
                avg_second = statistics.mean(moisture_second_half)
                delta = avg_second - avg_first

                if delta < -TREND_MOISTURE_THRESHOLD:
                    trends["soil_moisture_trend"] = "declining"
                    trends["soil_moisture_delta"] = delta
                elif delta > TREND_MOISTURE_THRESHOLD:
                    trends["soil_moisture_trend"] = "rising"
                    trends["soil_moisture_delta"] = delta
                else:
                    trends["soil_moisture_trend"] = "stable"
                    trends["soil_moisture_delta"] = delta

            # Temperature trend
            temp_first_half = [r.temperature for r in all_readings[:mid] if r.temperature]
            temp_second_half = [r.temperature for r in all_readings[mid:] if r.temperature]

            if temp_first_half and temp_second_half:
                avg_temp_first = statistics.mean(temp_first_half)
                avg_temp_second = statistics.mean(temp_second_half)
                delta_temp = avg_temp_second - avg_temp_first

                if delta_temp > TREND_TEMP_THRESHOLD:
                    trends["temperature_trend"] = "rising"
                    trends["temperature_delta"] = delta_temp
                elif delta_temp < -TREND_TEMP_THRESHOLD:
                    trends["temperature_trend"] = "falling"
                    trends["temperature_delta"] = delta_temp
                else:
                    trends["temperature_trend"] = "stable"
                    trends["temperature_delta"] = delta_temp

    # Analyze irrigation frequency (last 7 days)
    irrigators = db.get_irrigators_in_cluster(cluster_id)
    if irrigators:
        total_events = 0
        total_duration = 0
        for irrigator in irrigators:
            events = db.get_recent_events(irrigator.id, hours=7 * 24)
            irrigation_events = [e for e in events if e.action in ("start", "schedule_updated") and e.duration_minutes]
            total_events += len(irrigation_events)
            total_duration += sum(e.duration_minutes for e in irrigation_events)

        if total_events > 0:
            avg_per_day = total_events / 7
            avg_duration = total_duration / total_events

            # Low frequency: less than 1 irrigation per day with short durations
            if avg_per_day < 1 and avg_duration < 2:
                trends["irrigation_frequency_low"] = True
            # High frequency: more than 3 irrigations per day
            elif avg_per_day > 3:
                trends["irrigation_frequency_high"] = True

            trends["irrigation_avg_per_day"] = avg_per_day
            trends["irrigation_avg_duration"] = avg_duration

    return trends
