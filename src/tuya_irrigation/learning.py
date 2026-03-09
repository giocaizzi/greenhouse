#!/usr/bin/env python3
"""Irrigation learning engine.

Learns from post-irrigation soil moisture changes to:
- Estimate per-plant absorption rates (how much water each plant actually receives)
- Detect efficiency issues (blocked drips, under-irrigation)
- Identify unresolvable conflicts in single-irrigator clusters
- Track natural drainage rates

All computed from existing sensor_readings + irrigation_events data.
No additional tables needed.
"""

import statistics
import time
from dataclasses import dataclass

from tuya_irrigation.db import IrrigationDB
from tuya_irrigation.models import IrrigationEvent, Sensor
from tuya_irrigation.plant_db import get_plant_database


@dataclass
class IrrigationResponse:
    """Soil moisture change for one sensor after one irrigation event."""

    sensor_id: int
    plant_id: int | None
    sensor_name: str
    event_id: int
    event_timestamp: int
    duration_minutes: int
    pre_moisture: float  # Soil moisture before irrigation
    post_moisture: float  # Soil moisture after irrigation (best reading in window)
    delta: float  # post - pre
    delta_per_minute: float  # delta / duration
    reading_delay_seconds: int  # Time between irrigation and post reading


@dataclass
class PlantProfile:
    """Learned irrigation profile for a plant/sensor."""

    sensor_id: int
    plant_id: int | None
    sensor_name: str
    avg_absorption_per_minute: float  # Average soil moisture increase per minute of irrigation
    avg_drainage_per_hour: float  # Average soil moisture decrease per hour (natural drying)
    response_count: int  # Number of irrigation events analyzed
    min_delta: float  # Worst response ever
    max_delta: float  # Best response ever
    efficiency_score: float  # 0-1: how well this plant responds to irrigation


@dataclass
class Alert:
    """System alert for operator attention."""

    severity: str  # "warning" or "critical"
    alert_type: str  # "blocked_drip", "unresolvable_conflict", "rapid_drainage", "chronic_underwatering"
    message: str
    sensor_name: str | None = None
    data: dict | None = None


class IrrigationLearner:
    """Learns from historical irrigation data."""

    # Time windows for analysis
    PRE_WINDOW_SEC = 1800  # 30min before irrigation
    POST_WINDOW_SEC = 7200  # 2h after irrigation (water needs time to soak)
    MIN_POST_DELAY_SEC = 600  # Ignore readings < 10min after (water still distributing)

    def __init__(self, db: IrrigationDB):
        self.db = db

    def analyze_irrigation_response(self, event: IrrigationEvent) -> list[IrrigationResponse]:
        """Analyze soil moisture changes for all sensors after an irrigation event.

        Returns one IrrigationResponse per sensor in the cluster.
        """
        irrigator = self.db.get_irrigator(event.irrigator_id)
        if not irrigator:
            return []

        sensors = self.db.get_sensors_in_cluster(irrigator.cluster_id)
        if not sensors:
            return []

        responses = []
        for sensor in sensors:
            response = self._compute_sensor_response(sensor, event)
            if response:
                responses.append(response)

        return responses

    def _compute_sensor_response(self, sensor: Sensor, event: IrrigationEvent) -> IrrigationResponse | None:
        """Compute moisture delta for a single sensor around an irrigation event."""
        before_readings, after_readings = self.db.get_readings_around(
            sensor.id,
            event.timestamp,
            before_seconds=self.PRE_WINDOW_SEC,
            after_seconds=self.POST_WINDOW_SEC,
        )

        # Need at least one reading before and after
        pre_moisture_readings = [r for r in before_readings if r.soil_moisture is not None]
        post_moisture_readings = [
            r for r in after_readings
            if r.soil_moisture is not None and (r.timestamp - event.timestamp) >= self.MIN_POST_DELAY_SEC
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

    def get_plant_profile(self, sensor: Sensor, days: int = 30) -> PlantProfile | None:
        """Build a learned profile for a plant based on historical irrigation responses.

        Needs at least 3 irrigation events with sensor data to be meaningful.
        """
        # Get all irrigation events in the sensor's cluster
        irrigators = self.db.get_irrigators_in_cluster(sensor.cluster_id)
        if not irrigators:
            return None

        cutoff = int(time.time()) - (days * 86400)
        all_events = self.db.get_recent_events(irrigators[0].id, hours=days * 24)
        irrigation_events = [
            e for e in all_events
            if e.action == "start" and e.timestamp >= cutoff
        ]

        if not irrigation_events:
            return None

        # Compute response for each event
        responses = []
        for event in irrigation_events:
            response = self._compute_sensor_response(sensor, event)
            if response:
                responses.append(response)

        if not responses:
            return None

        deltas = [r.delta for r in responses]
        deltas_per_min = [r.delta_per_minute for r in responses]

        # Compute drainage rate from periods between irrigations
        drainage = self._compute_drainage_rate(sensor, days)

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

    def _compute_drainage_rate(self, sensor: Sensor, days: int = 30) -> float:
        """Compute average natural drainage rate (moisture loss per hour).

        Looks at periods between irrigations where moisture is declining.
        """
        readings = self.db.get_recent_readings(sensor.id, hours=days * 24)
        readings.sort(key=lambda r: r.timestamp)

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

    def detect_issues(self, cluster_id: int) -> list[Alert]:
        """Detect efficiency issues and unresolvable conflicts.

        Alert types:
        - blocked_drip: sensor shows no moisture change after irrigation
        - rapid_drainage: plant dries abnormally fast
        - chronic_underwatering: plant never reaches target moisture after irrigation
        - unresolvable_conflict: single irrigator can't satisfy all plants
        """
        alerts = []
        sensors = self.db.get_sensors_in_cluster(cluster_id)
        if not sensors:
            return alerts

        profiles = {}
        for sensor in sensors:
            profile = self.get_plant_profile(sensor)
            if profile:
                profiles[sensor.id] = profile

        if not profiles:
            return alerts  # Not enough data yet

        plant_db = get_plant_database()
        plants = self.db.get_plants_in_cluster(cluster_id)
        plant_care = {p.id: plant_db.get_care_data(species=p.species, category=p.category) for p in plants}

        for sensor in sensors:
            profile = profiles.get(sensor.id)
            if not profile or profile.response_count < 3:
                continue  # Not enough data

            # 1. Blocked drip: consistently low response
            if profile.efficiency_score < 0.3 and profile.avg_absorption_per_minute < 0.5:
                alerts.append(Alert(
                    severity="critical",
                    alert_type="blocked_drip",
                    message=(
                        f"🚫 {sensor.name}: minimal response to irrigation "
                        f"(avg +{profile.avg_absorption_per_minute:.1f}%/min, "
                        f"efficiency {profile.efficiency_score:.0%}). "
                        f"Check drip connection."
                    ),
                    sensor_name=sensor.name,
                    data={"absorption": profile.avg_absorption_per_minute, "efficiency": profile.efficiency_score},
                ))

            # 2. Rapid drainage
            if profile.avg_drainage_per_hour < -5:  # Losing >5% per hour
                alerts.append(Alert(
                    severity="warning",
                    alert_type="rapid_drainage",
                    message=(
                        f"💨 {sensor.name}: rapid drainage "
                        f"({profile.avg_drainage_per_hour:.1f}%/hr). "
                        f"Soil may not retain water well."
                    ),
                    sensor_name=sensor.name,
                    data={"drainage_rate": profile.avg_drainage_per_hour},
                ))

            # 3. Chronic underwatering: max delta never reaches target
            if sensor.plant_id and sensor.plant_id in plant_care:
                care = plant_care[sensor.plant_id]
                target = care.get("soil_moisture_target", "45-65")
                try:
                    target_min = float(target.split("-")[0])
                except (ValueError, IndexError):
                    target_min = 45.0

                # Check if recent readings ever reach target
                recent = self.db.get_recent_readings(sensor.id, hours=168)  # 7 days
                if recent:
                    max_recent = max((r.soil_moisture for r in recent if r.soil_moisture is not None), default=0)
                    if max_recent < target_min and profile.response_count >= 5:
                        alerts.append(Alert(
                            severity="warning",
                            alert_type="chronic_underwatering",
                            message=(
                                f"🏜️ {sensor.name}: soil never reaches target "
                                f"({max_recent:.0f}% peak vs {target_min:.0f}% target). "
                                f"Consider longer irrigation or check drip flow."
                            ),
                            sensor_name=sensor.name,
                            data={"max_recent": max_recent, "target_min": target_min},
                        ))

        # 4. Unresolvable conflict: check if profiles show incompatible needs
        if len(profiles) >= 2:
            alerts.extend(self._detect_conflicts(cluster_id, profiles, plant_care))

        return alerts

    def _detect_conflicts(self, cluster_id: int, profiles: dict, plant_care: dict) -> list[Alert]:
        """Detect unresolvable conflicts between plants in same cluster."""
        alerts = []
        sensors = self.db.get_sensors_in_cluster(cluster_id)

        # Get current moisture levels
        sensor_moisture = {}
        for sensor in sensors:
            readings = self.db.get_recent_readings(sensor.id, hours=6)
            moisture_values = [r.soil_moisture for r in readings if r.soil_moisture is not None]
            if moisture_values:
                sensor_moisture[sensor.id] = statistics.mean(moisture_values[-3:])  # Last 3 readings

        if len(sensor_moisture) < 2:
            return alerts

        # Check: one plant needs water, another is already saturated
        dry_sensors = []
        wet_sensors = []
        for sensor in sensors:
            if sensor.id not in sensor_moisture:
                continue
            moisture = sensor_moisture[sensor.id]

            # Determine target for this plant
            target_min, target_max = 45.0, 65.0
            if sensor.plant_id and sensor.plant_id in plant_care:
                target_str = plant_care[sensor.plant_id].get("soil_moisture_target", "45-65")
                try:
                    parts = target_str.split("-")
                    target_min, target_max = float(parts[0]), float(parts[1])
                except (ValueError, IndexError):
                    pass

            if moisture < target_min - 5:
                dry_sensors.append((sensor, moisture, target_min))
            elif moisture > target_max:
                wet_sensors.append((sensor, moisture, target_max))

        if dry_sensors and wet_sensors:
            # Estimate: would irrigating for the dry plant over-water the wet one?
            for dry_s, dry_m, dry_target in dry_sensors:
                dry_profile = profiles.get(dry_s.id)
                if not dry_profile or dry_profile.avg_absorption_per_minute <= 0:
                    continue
                # How many minutes needed to reach target?
                needed_delta = dry_target - dry_m
                needed_minutes = needed_delta / dry_profile.avg_absorption_per_minute

                for wet_s, wet_m, wet_target in wet_sensors:
                    wet_profile = profiles.get(wet_s.id)
                    if not wet_profile:
                        continue
                    # How much would wet plant gain in that time?
                    wet_gain = wet_profile.avg_absorption_per_minute * needed_minutes
                    projected_wet = wet_m + wet_gain

                    if projected_wet > 85:  # Would severely over-water
                        alerts.append(Alert(
                            severity="critical",
                            alert_type="unresolvable_conflict",
                            message=(
                                f"⚠️ Conflitto irrisolvibile: {dry_s.name} ha bisogno di "
                                f"~{needed_minutes:.0f}min di irrigazione ({dry_m:.0f}%→{dry_target:.0f}%), "
                                f"ma {wet_s.name} arriverebbe a {projected_wet:.0f}% "
                                f"(attuale {wet_m:.0f}%, max {wet_target:.0f}%). "
                                f"Considera: riposizionare drip, vaso separato, o irrigatore dedicato."
                            ),
                            sensor_name=f"{dry_s.name} vs {wet_s.name}",
                            data={
                                "dry_sensor": dry_s.name,
                                "dry_moisture": dry_m,
                                "wet_sensor": wet_s.name,
                                "wet_moisture": wet_m,
                                "needed_minutes": needed_minutes,
                                "projected_wet": projected_wet,
                            },
                        ))

        return alerts

    def generate_report(self, cluster_id: int) -> str:
        """Generate a human-readable learning report for a cluster."""
        lines = []
        sensors = self.db.get_sensors_in_cluster(cluster_id)

        if not sensors:
            return "No sensors in cluster."

        lines.append("📊 Irrigation Learning Report")
        lines.append("=" * 40)

        for sensor in sensors:
            profile = self.get_plant_profile(sensor)
            if not profile:
                lines.append(f"\n🌱 {sensor.name}: insufficient data (need more irrigation cycles)")
                continue

            lines.append(f"\n🌱 {sensor.name}")
            lines.append(f"   Data points: {profile.response_count} irrigation events")
            lines.append(f"   Absorption: {profile.avg_absorption_per_minute:+.1f}%/min of irrigation")
            lines.append(f"   Drainage: {profile.avg_drainage_per_hour:.1f}%/hr (natural drying)")
            lines.append(f"   Response range: {profile.min_delta:+.0f}% to {profile.max_delta:+.0f}%")
            lines.append(f"   Efficiency: {profile.efficiency_score:.0%}")

            if profile.efficiency_score < 0.5:
                lines.append("   ⚠️ Low efficiency — check drip positioning")

        # Alerts
        alerts = self.detect_issues(cluster_id)
        if alerts:
            lines.append(f"\n{'=' * 40}")
            lines.append("🚨 Alerts")
            for alert in alerts:
                lines.append(f"   [{alert.severity.upper()}] {alert.message}")

        return "\n".join(lines)
