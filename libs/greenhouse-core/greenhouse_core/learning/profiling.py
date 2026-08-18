"""Profile building from historical irrigation data."""

import statistics
import time

from greenhouse_core.learning.models import IrrigationResponse, PlantProfile
from greenhouse_core.logic.cleaning import clean_readings, clean_readings_around
from greenhouse_core.models import IrrigationEvent, Sensor
from greenhouse_core.repository import IrrigationRepository

# Time windows for analysis
PRE_WINDOW_SEC = 1800  # 30min before irrigation
POST_WINDOW_SEC = 7200  # 2h after irrigation (water needs time to soak)
MIN_POST_DELAY_SEC = 600  # Ignore readings < 10min after (water still distributing)


def compute_sensor_response(
    db: IrrigationRepository,
    sensor: Sensor,
    event: IrrigationEvent,
) -> IrrigationResponse | None:
    """Compute moisture delta for a single sensor around an irrigation event."""
    before_rows, after_rows = db.get_readings_around(
        sensor.id,
        event.timestamp,
        before_seconds=PRE_WINDOW_SEC,
        after_seconds=POST_WINDOW_SEC,
    )
    # Cleaned view — the post reading is picked as the window's *maximum*
    # (peak absorption), which is precisely the sample a spike would win.
    before_readings, after_readings = clean_readings_around(before_rows, after_rows)

    # Need at least one reading before and after
    pre_moisture_readings = [r for r in before_readings if r.soil_moisture is not None]
    post_moisture_readings = [
        r
        for r in after_readings
        if r.soil_moisture is not None and (r.timestamp - event.timestamp) >= MIN_POST_DELAY_SEC
    ]

    if not pre_moisture_readings or not post_moisture_readings:
        return None

    # Pre: use the last reading before irrigation
    pre = pre_moisture_readings[-1]

    # Post: use the reading with highest moisture (peak absorption)
    post = max(post_moisture_readings, key=lambda r: r.soil_moisture)

    duration = event.duration_minutes or 2
    delta = post.soil_moisture - pre.soil_moisture

    return IrrigationResponse(
        sensor_id=sensor.id,
        plant_id=sensor.plant_id,
        sensor_name=sensor.name,
        event_id=event.id,
        event_timestamp=event.timestamp,
        duration_minutes=duration,
        pre_moisture=pre.soil_moisture,
        post_moisture=post.soil_moisture,
        delta=delta,
        delta_per_minute=delta / duration if duration > 0 else 0,
        reading_delay_seconds=post.timestamp - event.timestamp,
    )


def analyze_irrigation_response(
    db: IrrigationRepository,
    event: IrrigationEvent,
) -> list[IrrigationResponse]:
    """Analyze soil moisture changes for all sensors after an irrigation event."""
    irrigator = db.get_irrigator(event.irrigator_id)
    if not irrigator:
        return []

    sensors = db.get_sensors_in_cluster(irrigator.cluster_id)
    if not sensors:
        return []

    responses = []
    for sensor in sensors:
        response = compute_sensor_response(db, sensor, event)
        if response:
            responses.append(response)

    return responses


def get_plant_profile(
    db: IrrigationRepository,
    sensor: Sensor,
    days: int = 30,
) -> PlantProfile | None:
    """Build a learned profile for a plant based on historical irrigation responses.

    Needs at least 3 irrigation events with sensor data to be meaningful.
    """
    irrigator = db.get_irrigator_for_cluster(sensor.cluster_id)
    if irrigator is None:
        return None

    cutoff = int(time.time()) - (days * 86400)
    all_events = db.get_recent_events(irrigator.id, hours=days * 24)
    irrigation_events = [e for e in all_events if e.action == "start" and e.timestamp >= cutoff]

    if not irrigation_events:
        return None

    # Compute response for each event
    responses = []
    for event in irrigation_events:
        response = compute_sensor_response(db, sensor, event)
        if response:
            responses.append(response)

    if not responses:
        return None

    deltas = [r.delta for r in responses]
    deltas_per_min = [r.delta_per_minute for r in responses]

    # Compute drainage rate from periods between irrigations
    drainage = compute_drainage_rate(db, sensor, days)

    # Efficiency: how consistently does irrigation increase moisture?
    positive_responses = sum(1 for d in deltas if d > 2)  # >2% increase counts
    efficiency = positive_responses / len(deltas) if deltas else 0

    return PlantProfile(
        sensor_id=sensor.id,
        plant_id=sensor.plant_id,
        sensor_name=sensor.name,
        avg_absorption_per_minute=statistics.mean(deltas_per_min),
        avg_drainage_per_hour=drainage,
        response_count=len(responses),
        min_delta=min(deltas),
        max_delta=max(deltas),
        efficiency_score=efficiency,
    )


def compute_drainage_rate(db: IrrigationRepository, sensor: Sensor, days: int = 30) -> float:
    """Compute average natural drainage rate (moisture loss per hour).

    Looks at periods between irrigations where moisture is declining.
    """
    # Cleaned view, chronological: drainage is read off consecutive deltas, so
    # a spike would register as both an impossible gain and a fake steep loss.
    readings = clean_readings(db.get_recent_readings(sensor.id, hours=days * 24))

    # Find consecutive readings where moisture is declining
    declines = []
    for i in range(1, len(readings)):
        prev, curr = readings[i - 1], readings[i]
        if prev.soil_moisture is not None and curr.soil_moisture is not None:
            delta = curr.soil_moisture - prev.soil_moisture
            hours = (curr.timestamp - prev.timestamp) / 3600
            if delta < 0 and 0.1 < hours < 12:  # Reasonable time window
                declines.append(delta / hours)

    if not declines:
        return 0.0

    return statistics.mean(declines)  # Negative value (loss per hour)
