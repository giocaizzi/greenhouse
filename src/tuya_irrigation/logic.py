#!/usr/bin/env python3
"""Smart irrigation logic based on sensor data, plant needs, and scientific literature."""

import statistics
import time

from tuya_irrigation.db import IrrigationDB
from tuya_irrigation.models import IrrigationConfig
from tuya_irrigation.plant_db import get_plant_database


class IrrigationLogic:
    """Smart irrigation decision engine using evidence-based plant data."""

    def __init__(self, db: IrrigationDB):
        self.db = db
        self.plant_db = get_plant_database()

    def decide_for_cluster(self, cluster_id: int, current_temp: float | None = None) -> dict | None:
        """
        Decide if irrigation is needed for a cluster.

        Returns dict with:
        - action: "irrigate" or "skip"
        - duration_minutes: suggested duration
        - interval_hours: suggested interval
        - reason: explanation of decision
        - confidence: 0-1 confidence score
        - stress_indicators: dict of detected stress conditions (if any)

        Returns None if not enough data to decide.
        """
        cluster = self.db.get_cluster(cluster_id)
        if not cluster:
            return None

        plants = self.db.get_plants_in_cluster(cluster_id)
        if not plants:
            return {"action": "skip", "reason": "No plants in cluster", "confidence": 0}

        sensors = self.db.get_sensors_in_cluster(cluster_id)
        config = self.db.get_irrigation_config(cluster_id)

        # GLOBAL COOLDOWN: Check recent irrigation events from ANY trigger
        # This prevents over-watering even if manual/test irrigations happened
        min_interval_hours = 6  # Minimum cooldown between any irrigations
        irrigators = self.db.get_irrigators_in_cluster(cluster_id)
        if irrigators:
            recent_events = self.db.get_recent_events(irrigators[0].id, hours=min_interval_hours)
            irrigation_events = [e for e in recent_events if e.action == "start"]
            if irrigation_events:
                last_event = irrigation_events[0]
                trigger = last_event.triggered_by
                hours_ago = (int(time.time()) - last_event.timestamp) / 3600
                return {
                    "action": "skip",
                    "duration_minutes": 2,
                    "interval_hours": min_interval_hours,
                    "reason": f"cooldown active (last irrigation {hours_ago:.1f}h ago, trigger: {trigger})",
                    "confidence": 0.9,
                }

        # Collect recent sensor data
        sensor_data = self._get_recent_sensor_data(cluster_id, hours=24)

        # Analyze historical trends and stress conditions
        trends = self._analyze_historical_trends(cluster_id)
        stress = self._detect_stress_conditions(cluster_id, sensor_data, trends)

        # Check learned efficiency issues (non-blocking, advisory)
        try:
            from tuya_irrigation.learning import IrrigationLearner

            learner = IrrigationLearner(self.db)
            learning_alerts = learner.detect_issues(cluster_id)
            if learning_alerts:
                stress["learning_alerts"] = [
                    {"type": a.alert_type, "severity": a.severity, "message": a.message}
                    for a in learning_alerts
                ]
        except Exception:
            pass  # Learning is advisory, never blocks decisions

        # Get plant requirements from database
        plant_care_data = [self.plant_db.get_care_data(species=p.species, category=p.category) for p in plants]

        # Analyze aggregate needs
        ideal_temp_range = self._get_ideal_temp_range(plant_care_data)
        ideal_humidity_range = self._get_ideal_humidity_range(plant_care_data)
        water_needs_level = self._analyze_water_needs(plant_care_data)

        # Decision logic
        decision = {
            "action": "skip",
            "duration_minutes": 2,  # Default to 2 min (weak drippers)
            "interval_hours": 12,
            "reason": "",
            "confidence": 0.5,
        }

        # If no sensors, use temperature-based fallback
        if not sensors or not sensor_data:
            return self._temperature_based_decision(
                current_temp, water_needs_level, ideal_temp_range, config, cluster_id
            )

        # Smart decision based on sensor data + trends
        avg_temp = sensor_data.get("avg_temperature")
        avg_humidity = sensor_data.get("avg_humidity")
        avg_soil_moisture = sensor_data.get("avg_soil_moisture")

        reasons = []

        # PRIORITY 1: Critical stress conditions (override everything)
        if stress.get("water_stress"):
            decision["action"] = "irrigate"
            decision["duration_minutes"] = 3
            decision["interval_hours"] = 6
            reasons.append(f"⚠️ water stress detected ({stress['water_stress']})")
            decision["confidence"] = 0.95
            decision["reason"] = "; ".join(reasons)
            decision["stress_indicators"] = stress
            decision["trends"] = trends
            return decision
        elif stress.get("over_watering"):
            decision["action"] = "skip"
            decision["interval_hours"] = 24
            reasons.append(f"⚠️ over-watering detected ({stress['over_watering']})")
            decision["confidence"] = 0.9
            decision["reason"] = "; ".join(reasons)
            decision["stress_indicators"] = stress
            decision["trends"] = trends
            return decision

        # PRIORITY 2: Soil moisture is the primary indicator
        # With a single irrigator serving multiple plants, we use conservative logic:
        # - Irrigate if the DRIEST plant needs water AND no plant would be over-watered
        # - If conflict (one dry, one wet): shorter duration to help dry without flooding wet
        if avg_soil_moisture is not None:
            # Get target soil moisture from plant database
            target_ranges = [
                self._parse_moisture_target(d.get("soil_moisture_target", "45-65")) for d in plant_care_data
            ]
            target_min = min(r[0] for r in target_ranges)
            target_max = max(r[1] for r in target_ranges)

            min_soil = sensor_data.get("min_soil_moisture", avg_soil_moisture)
            max_soil = sensor_data.get("max_soil_moisture", avg_soil_moisture)
            per_sensor = sensor_data.get("per_sensor", [])

            # Detect conflict: one plant dry, another already wet
            has_conflict = (min_soil < target_min) and (max_soil > target_max - 10)

            if has_conflict:
                # Conflict: irrigate conservatively (short burst to help dry plant
                # without over-watering the wet one)
                decision["action"] = "irrigate"
                decision["duration_minutes"] = 1  # Minimal
                decision["interval_hours"] = 8
                dry_names = [s["name"] for s in per_sensor
                             if s.get("avg_soil_moisture") is not None and s["avg_soil_moisture"] < target_min]
                wet_names = [s["name"] for s in per_sensor
                             if s.get("avg_soil_moisture") is not None and s["avg_soil_moisture"] > target_max - 10]
                reasons.append(
                    f"⚠️ conflict: dry={min_soil:.0f}% ({', '.join(dry_names) or '?'}), "
                    f"wet={max_soil:.0f}% ({', '.join(wet_names) or '?'}) — short burst"
                )
                decision["confidence"] = 0.65
            elif min_soil < target_min - 10:
                # Driest plant is very dry, no conflict
                decision["action"] = "irrigate"
                decision["duration_minutes"] = 3
                decision["interval_hours"] = 8
                reasons.append(f"soil very dry (driest={min_soil:.0f}% < {target_min}%)")
                decision["confidence"] = 0.9
            elif min_soil < target_min:
                # Driest plant is moderately dry
                decision["action"] = "irrigate"
                decision["duration_minutes"] = 2
                decision["interval_hours"] = 12
                reasons.append(f"soil moderately dry (driest={min_soil:.0f}%)")
                decision["confidence"] = 0.8
            elif avg_soil_moisture <= target_max:
                # All plants adequate
                decision["action"] = "skip"
                reasons.append(f"soil moisture adequate (range={min_soil:.0f}-{max_soil:.0f}%)")
                decision["confidence"] = 0.7
            else:
                # Too wet
                decision["action"] = "skip"
                reasons.append(f"soil too wet (wettest={max_soil:.0f}% > {target_max}%)")
                decision["confidence"] = 0.8

        # Temperature adjustment
        if avg_temp is not None and ideal_temp_range:
            if avg_temp > ideal_temp_range[1] + 3:
                # Hot conditions → increase frequency
                decision["interval_hours"] = max(6, decision["interval_hours"] - 4)
                reasons.append(f"temp above ideal ({avg_temp:.0f}°C > {ideal_temp_range[1]:.0f}°C)")
            elif avg_temp < ideal_temp_range[0] - 3:
                # Cold conditions → decrease frequency
                decision["interval_hours"] = min(24, decision["interval_hours"] + 6)
                reasons.append(f"temp below ideal ({avg_temp:.0f}°C < {ideal_temp_range[0]:.0f}°C)")

        # Humidity adjustment
        if avg_humidity is not None and ideal_humidity_range:
            if avg_humidity < ideal_humidity_range[0] - 10:
                # Very dry air → increase slightly
                decision["interval_hours"] = max(6, decision["interval_hours"] - 2)
                reasons.append(f"low humidity ({avg_humidity:.0f}%)")

        # Water needs adjustment
        self.plant_db.get_water_needs_info(water_needs_level)
        if water_needs_level == "high":
            decision["duration_minutes"] = max(2, decision["duration_minutes"] + 1)
            decision["interval_hours"] = max(6, decision["interval_hours"] - 2)
        elif water_needs_level == "low":
            decision["duration_minutes"] = max(1, decision["duration_minutes"] - 1)
            decision["interval_hours"] = min(24, decision["interval_hours"] + 4)

        # Apply trend-based adjustments
        if trends.get("soil_moisture_trend") == "declining":
            decision["interval_hours"] = max(6, decision["interval_hours"] - 2)
            reasons.append("📉 soil moisture declining")
        elif trends.get("soil_moisture_trend") == "rising":
            decision["interval_hours"] = min(24, decision["interval_hours"] + 2)
            reasons.append("📈 soil moisture rising")

        if trends.get("temperature_trend") == "rising" and avg_temp and avg_temp > 25:
            decision["interval_hours"] = max(6, decision["interval_hours"] - 2)
            reasons.append("🌡️ temperature rising + hot")

        if trends.get("irrigation_frequency_low"):
            decision["duration_minutes"] = min(5, decision["duration_minutes"] + 1)
            reasons.append("📊 recent under-watering pattern")

        decision["reason"] = "; ".join(reasons) if reasons else "no specific conditions"
        decision["stress_indicators"] = stress
        decision["trends"] = trends
        return decision

    def _get_recent_sensor_data(self, cluster_id: int, hours: int = 24) -> dict:
        """Get sensor data from recent readings, per-sensor and aggregated.

        Returns dict with:
        - avg_temperature, avg_humidity, avg_soil_moisture, avg_light (cluster-wide)
        - min_soil_moisture, max_soil_moisture (for conflict detection)
        - per_sensor: list of {sensor_id, plant_id, name, avg_soil, avg_temp, ...}
        """
        sensors = self.db.get_sensors_in_cluster(cluster_id)
        if not sensors:
            return {}

        all_temps = []
        all_humidity = []
        all_soil = []
        all_light = []
        per_sensor = []

        for sensor in sensors:
            readings = self.db.get_recent_readings(sensor.id, hours=hours)
            s_temps = []
            s_humidity = []
            s_soil = []

            for r in readings:
                if r.temperature is not None:
                    all_temps.append(r.temperature)
                    s_temps.append(r.temperature)
                if r.humidity is not None:
                    all_humidity.append(r.humidity)
                    s_humidity.append(r.humidity)
                if r.soil_moisture is not None:
                    all_soil.append(r.soil_moisture)
                    s_soil.append(r.soil_moisture)
                if r.light is not None:
                    all_light.append(r.light)

            sensor_info = {
                "sensor_id": sensor.id,
                "plant_id": sensor.plant_id,
                "name": sensor.name,
            }
            if s_temps:
                sensor_info["avg_temperature"] = statistics.mean(s_temps)
            if s_humidity:
                sensor_info["avg_humidity"] = statistics.mean(s_humidity)
            if s_soil:
                sensor_info["avg_soil_moisture"] = statistics.mean(s_soil)
            per_sensor.append(sensor_info)

        data = {"per_sensor": per_sensor}
        if all_temps:
            data["avg_temperature"] = statistics.mean(all_temps)
        if all_humidity:
            data["avg_humidity"] = statistics.mean(all_humidity)
        if all_soil:
            data["avg_soil_moisture"] = statistics.mean(all_soil)
            data["min_soil_moisture"] = min(all_soil)
            data["max_soil_moisture"] = max(all_soil)
        if all_light:
            data["avg_light"] = statistics.mean(all_light)

        return data

    def _analyze_water_needs(self, plant_care_data: list[dict]) -> str:
        """Determine average water needs level for cluster from plant database."""
        needs_map = {"low": 1, "medium": 2, "high": 3}
        values = [needs_map.get(d.get("water_needs", "medium"), 2) for d in plant_care_data]
        if not values:
            return "medium"
        avg = statistics.mean(values)
        if avg < 1.5:
            return "low"
        elif avg > 2.5:
            return "high"
        return "medium"

    def _get_ideal_temp_range(self, plant_care_data: list[dict]) -> tuple[float, float] | None:
        """Get ideal temperature range for cluster from plant database."""
        mins = [d.get("ideal_temp_min_c") for d in plant_care_data if d.get("ideal_temp_min_c")]
        maxs = [d.get("ideal_temp_max_c") for d in plant_care_data if d.get("ideal_temp_max_c")]
        if not mins or not maxs:
            return None
        return (min(mins), max(maxs))

    def _get_ideal_humidity_range(self, plant_care_data: list[dict]) -> tuple[float, float] | None:
        """Get ideal humidity range for cluster from plant database."""
        mins = [d.get("ideal_humidity_min") for d in plant_care_data if d.get("ideal_humidity_min")]
        maxs = [d.get("ideal_humidity_max") for d in plant_care_data if d.get("ideal_humidity_max")]
        if not mins or not maxs:
            return None
        return (min(mins), max(maxs))

    def _parse_moisture_target(self, target: str) -> tuple[float, float]:
        """Parse soil moisture target range string like '45-65' to tuple (45, 65)."""
        try:
            parts = target.split("-")
            return (float(parts[0]), float(parts[1]))
        except Exception:
            return (45.0, 65.0)  # Default

    def _analyze_historical_trends(self, cluster_id: int) -> dict:
        """
        Analyze historical sensor and irrigation data to detect trends.

        Returns dict with:
        - soil_moisture_trend: "rising", "declining", "stable", or None
        - temperature_trend: "rising", "falling", "stable", or None
        - irrigation_frequency_low: bool (under-watering pattern)
        - irrigation_frequency_high: bool (over-watering pattern)
        """
        trends = {}

        # Get sensor readings for last 48h
        sensors = self.db.get_sensors_in_cluster(cluster_id)
        if sensors:
            # Collect soil moisture history
            all_readings = []
            for sensor in sensors:
                readings = self.db.get_recent_readings(sensor.id, hours=48)
                all_readings.extend(readings)

            if len(all_readings) >= 4:  # Need at least 4 readings for trend
                all_readings.sort(key=lambda r: r.timestamp)

                # Soil moisture trend (compare first half vs second half)
                mid = len(all_readings) // 2
                moisture_first_half = [r.soil_moisture for r in all_readings[:mid] if r.soil_moisture]
                moisture_second_half = [r.soil_moisture for r in all_readings[mid:] if r.soil_moisture]

                if moisture_first_half and moisture_second_half:
                    avg_first = statistics.mean(moisture_first_half)
                    avg_second = statistics.mean(moisture_second_half)
                    delta = avg_second - avg_first

                    if delta < -5:  # Declining by more than 5%
                        trends["soil_moisture_trend"] = "declining"
                        trends["soil_moisture_delta"] = delta
                    elif delta > 5:  # Rising by more than 5%
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

                    if delta_temp > 2:
                        trends["temperature_trend"] = "rising"
                        trends["temperature_delta"] = delta_temp
                    elif delta_temp < -2:
                        trends["temperature_trend"] = "falling"
                        trends["temperature_delta"] = delta_temp
                    else:
                        trends["temperature_trend"] = "stable"
                        trends["temperature_delta"] = delta_temp

        # Analyze irrigation frequency (last 7 days)
        irrigators = self.db.get_irrigators_in_cluster(cluster_id)
        if irrigators:
            total_events = 0
            total_duration = 0
            for irrigator in irrigators:
                events = self.db.get_recent_events(irrigator.id, hours=7 * 24)
                irrigation_events = [
                    e for e in events if e.action in ("start", "schedule_updated") and e.duration_minutes
                ]
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

    def _detect_stress_conditions(self, cluster_id: int, sensor_data: dict, trends: dict) -> dict:
        """
        Detect stress conditions based on sensor data and trends.

        Returns dict with stress indicators:
        - water_stress: description if detected
        - heat_stress: description if detected
        - over_watering: description if detected
        """
        stress = {}

        # Water stress: soil moisture consistently low + declining trend
        avg_soil = sensor_data.get("avg_soil_moisture")
        if avg_soil:
            # Critical: below 30% OR below 40% with steep decline
            if avg_soil < 30:
                stress["water_stress"] = f"critical low ({avg_soil:.0f}%)"
                if trends.get("soil_moisture_trend") == "declining":
                    stress["water_stress"] += " + declining"
            elif avg_soil < 40 and trends.get("soil_moisture_trend") == "declining":
                delta = trends.get("soil_moisture_delta", 0)
                if delta < -10:  # Steep decline
                    stress["water_stress"] = f"low ({avg_soil:.0f}%) + steep decline ({delta:.0f}%)"

        # Heat stress: prolonged high temperature
        avg_temp = sensor_data.get("avg_temperature")
        plants = self.db.get_plants_in_cluster(cluster_id)
        if avg_temp and plants:
            plant_care_data = [self.plant_db.get_care_data(species=p.species, category=p.category) for p in plants]
            temp_range = self._get_ideal_temp_range(plant_care_data)
            if temp_range and avg_temp > temp_range[1] + 5:
                if trends.get("temperature_trend") == "rising":
                    stress["heat_stress"] = f"high temp ({avg_temp:.0f}°C) + rising"
                else:
                    stress["heat_stress"] = f"above ideal ({avg_temp:.0f}°C > {temp_range[1]:.0f}°C)"

        # Over-watering: soil consistently wet + high irrigation frequency
        if avg_soil and avg_soil > 70:  # Above 70% is excessive
            if trends.get("irrigation_frequency_high"):
                stress["over_watering"] = f"soil saturated ({avg_soil:.0f}%) + high frequency"
            elif trends.get("soil_moisture_trend") == "rising":
                stress["over_watering"] = f"soil very wet ({avg_soil:.0f}%) + rising"

        return stress

    def _temperature_based_decision(
        self,
        temp: float | None,
        water_needs: str,
        temp_range: tuple[float, float] | None,
        config: IrrigationConfig | None,
        cluster_id: int,
    ) -> dict:
        """Fallback to temperature-based logic when no sensor data available."""
        if temp is None:
            # No data at all → use config or conservative default
            if config:
                return {
                    "action": "skip" if config.mode == "manual" else "irrigate",
                    "duration_minutes": config.duration_minutes or 2,
                    "interval_hours": config.interval_hours or 12,
                    "reason": "using configured schedule (no sensor data)",
                    "confidence": 0.3,
                }
            return {
                "action": "skip",
                "duration_minutes": 2,
                "interval_hours": 12,
                "reason": "insufficient data",
                "confidence": 0.2,
            }

        # Temperature-based buckets
        if temp <= 18:
            interval = 24
        elif temp <= 24:
            interval = 12
        elif temp <= 28:
            interval = 8
        else:
            interval = 6

        # Adjust for water needs
        if water_needs == "high":
            interval = max(6, interval - 4)
        elif water_needs == "low":
            interval = min(24, interval + 6)

        # CRITICAL: Check last irrigation time to respect cooldown
        irrigators = self.db.get_irrigators_in_cluster(cluster_id)
        if irrigators:
            recent_events = self.db.get_recent_events(irrigators[0].id, hours=interval)
            irrigation_events = [e for e in recent_events if e.action in ("start", "schedule_updated")]
            if irrigation_events:
                # Found recent irrigation → skip
                return {
                    "action": "skip",
                    "duration_minutes": 2,
                    "interval_hours": interval,
                    "reason": f"cooldown active (last irrigation < {interval}h ago)",
                    "confidence": 0.9,
                }

        # No recent irrigation → irrigate
        return {
            "action": "irrigate",
            "duration_minutes": 2,
            "interval_hours": interval,
            "reason": f"temperature-based ({temp:.0f}°C, {water_needs} water needs, evidence-based data)",
            "confidence": 0.6,
        }
