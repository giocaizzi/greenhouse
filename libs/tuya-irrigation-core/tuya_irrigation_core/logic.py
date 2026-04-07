#!/usr/bin/env python3
"""Smart irrigation logic based on sensor data, plant needs, and scientific literature."""

import statistics
import time

from tuya_irrigation_core.constants import (
    CONFIDENCE_CONFIG_FALLBACK,
    CONFIDENCE_CONFLICT,
    CONFIDENCE_COOLDOWN,
    CONFIDENCE_CRITICAL_STRESS,
    CONFIDENCE_NO_DATA,
    CONFIDENCE_OVER_WATERING,
    CONFIDENCE_SENSOR_ADEQUATE,
    CONFIDENCE_SENSOR_DRY,
    CONFIDENCE_SENSOR_VERY_DRY,
    CONFIDENCE_SENSOR_WET,
    CONFIDENCE_TEMP_FALLBACK,
    CONFIDENCE_WATER_WARNING,
    CONFLICT_DURATION_MINUTES,
    CONFLICT_INTERVAL_HOURS,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_INTERVAL_HOURS,
    DEFAULT_SOIL_MOISTURE_MAX,
    DEFAULT_SOIL_MOISTURE_MIN,
    LIGHT_BRIGHT,
    LIGHT_DARK,
    LIGHT_VERY_BRIGHT,
    LIGHT_VERY_DARK,
    MAX_DURATION_MINUTES,
    MAX_INTERVAL_HOURS,
    MIN_COOLDOWN_HOURS,
    MIN_INTERVAL_HOURS,
    SOIL_MOISTURE_CRITICAL,
    SOIL_MOISTURE_LOW,
    SOIL_MOISTURE_SATURATED,
    STRESS_DURATION_MINUTES,
    STRESS_INTERVAL_HOURS,
    TEMP_COLD,
    TEMP_HOT,
    TEMP_WARM,
    TREND_MIN_READINGS,
    TREND_MOISTURE_THRESHOLD,
    TREND_TEMP_THRESHOLD,
)
from tuya_irrigation_core.models import IrrigationConfig
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_core.utils import effective_light_threshold, seasonal_light_factor


class IrrigationLogic:
    """Smart irrigation decision engine using evidence-based plant data."""

    def __init__(self, db: IrrigationRepository, plant_db: PlantDatabase):
        self.db = db
        self.plant_db = plant_db

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
        irrigators = self.db.get_irrigators_in_cluster(cluster_id)
        if irrigators:
            recent_events = self.db.get_recent_events(irrigators[0].id, hours=MIN_COOLDOWN_HOURS)
            irrigation_events = [e for e in recent_events if e.action == "start"]
            if irrigation_events:
                last_event = irrigation_events[0]
                trigger = last_event.triggered_by
                hours_ago = (int(time.time()) - last_event.timestamp) / 3600
                return {
                    "action": "skip",
                    "duration_minutes": DEFAULT_DURATION_MINUTES,
                    "interval_hours": MIN_COOLDOWN_HOURS,
                    "reason": f"cooldown active (last irrigation {hours_ago:.1f}h ago, trigger: {trigger})",
                    "confidence": CONFIDENCE_COOLDOWN,
                }

        # Collect recent sensor data
        sensor_data = self._get_recent_sensor_data(cluster_id, hours=24)

        # Analyze historical trends and stress conditions
        trends = self._analyze_historical_trends(cluster_id)
        stress = self._detect_stress_conditions(cluster_id, sensor_data, trends)

        # Check learned efficiency issues (non-blocking, advisory)
        try:
            from tuya_irrigation_core.learning import IrrigationLearner

            learner = IrrigationLearner(self.db, self.plant_db)
            learning_alerts = learner.detect_issues(cluster_id)
            if learning_alerts:
                stress["learning_alerts"] = [
                    {"type": a.alert_type, "severity": a.severity, "message": a.message} for a in learning_alerts
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
            "duration_minutes": DEFAULT_DURATION_MINUTES,
            "interval_hours": DEFAULT_INTERVAL_HOURS,
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
        avg_humidity = sensor_data.get("avg_env_humidity")
        avg_soil_moisture = sensor_data.get("avg_soil_moisture")

        reasons = []

        # PRIORITY 0: Device water_warning (sensor's own alert — high confidence)
        if stress.get("water_warning"):
            decision["action"] = "irrigate"
            decision["duration_minutes"] = STRESS_DURATION_MINUTES
            decision["interval_hours"] = STRESS_INTERVAL_HOURS
            reasons.append(f"⚠️ sensor alert: {stress['water_warning']}")
            decision["confidence"] = CONFIDENCE_WATER_WARNING
            decision["reason"] = "; ".join(reasons)
            decision["stress_indicators"] = stress
            decision["trends"] = trends
            return decision

        # PRIORITY 1: Critical stress conditions (override everything)
        if stress.get("water_stress"):
            decision["action"] = "irrigate"
            decision["duration_minutes"] = STRESS_DURATION_MINUTES
            decision["interval_hours"] = STRESS_INTERVAL_HOURS
            reasons.append(f"⚠️ water stress detected ({stress['water_stress']})")
            decision["confidence"] = CONFIDENCE_CRITICAL_STRESS
            decision["reason"] = "; ".join(reasons)
            decision["stress_indicators"] = stress
            decision["trends"] = trends
            return decision
        elif stress.get("over_watering"):
            decision["action"] = "skip"
            decision["interval_hours"] = MAX_INTERVAL_HOURS
            reasons.append(f"⚠️ over-watering detected ({stress['over_watering']})")
            decision["confidence"] = CONFIDENCE_OVER_WATERING
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
                decision["duration_minutes"] = CONFLICT_DURATION_MINUTES
                decision["interval_hours"] = CONFLICT_INTERVAL_HOURS
                dry_names = [
                    s["name"]
                    for s in per_sensor
                    if s.get("avg_soil_moisture") is not None and s["avg_soil_moisture"] < target_min
                ]
                wet_names = [
                    s["name"]
                    for s in per_sensor
                    if s.get("avg_soil_moisture") is not None and s["avg_soil_moisture"] > target_max - 10
                ]
                reasons.append(
                    f"⚠️ conflict: dry={min_soil:.0f}% ({', '.join(dry_names) or '?'}), "
                    f"wet={max_soil:.0f}% ({', '.join(wet_names) or '?'}) — short burst"
                )
                decision["confidence"] = CONFIDENCE_CONFLICT
            elif min_soil < target_min - 10:
                # Driest plant is very dry, no conflict
                decision["action"] = "irrigate"
                decision["duration_minutes"] = STRESS_DURATION_MINUTES
                decision["interval_hours"] = CONFLICT_INTERVAL_HOURS
                reasons.append(f"soil very dry (driest={min_soil:.0f}% < {target_min}%)")
                decision["confidence"] = CONFIDENCE_SENSOR_VERY_DRY
            elif min_soil < target_min:
                # Driest plant is moderately dry
                decision["action"] = "irrigate"
                decision["duration_minutes"] = DEFAULT_DURATION_MINUTES
                decision["interval_hours"] = DEFAULT_INTERVAL_HOURS
                reasons.append(f"soil moderately dry (driest={min_soil:.0f}%)")
                decision["confidence"] = CONFIDENCE_SENSOR_DRY
            elif avg_soil_moisture <= target_max:
                # All plants adequate
                decision["action"] = "skip"
                reasons.append(f"soil moisture adequate (range={min_soil:.0f}-{max_soil:.0f}%)")
                decision["confidence"] = CONFIDENCE_SENSOR_ADEQUATE
            else:
                # Too wet
                decision["action"] = "skip"
                reasons.append(f"soil too wet (wettest={max_soil:.0f}% > {target_max}%)")
                decision["confidence"] = CONFIDENCE_SENSOR_WET

        # Temperature adjustment
        if avg_temp is not None and ideal_temp_range:
            if avg_temp > ideal_temp_range[1] + 3:
                # Hot conditions → increase frequency
                decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 4)
                reasons.append(f"temp above ideal ({avg_temp:.0f}°C > {ideal_temp_range[1]:.0f}°C)")
            elif avg_temp < ideal_temp_range[0] - 3:
                # Cold conditions → decrease frequency
                decision["interval_hours"] = min(MAX_INTERVAL_HOURS, decision["interval_hours"] + 6)
                reasons.append(f"temp below ideal ({avg_temp:.0f}°C < {ideal_temp_range[0]:.0f}°C)")

        # Env humidity adjustment
        if avg_humidity is not None and ideal_humidity_range:
            if avg_humidity < ideal_humidity_range[0] - 20:
                # Very dry air → high transpiration → irrigate more frequently
                decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 3)
                reasons.append(f"💨 very dry air ({avg_humidity:.0f}% << ideal {ideal_humidity_range[0]:.0f}%)")
            elif avg_humidity < ideal_humidity_range[0] - 5:
                decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 1)
                reasons.append(f"💨 dry air ({avg_humidity:.0f}%)")
            elif avg_humidity > ideal_humidity_range[1] + 10:
                # Very humid → less transpiration → reduce frequency
                decision["interval_hours"] = min(MAX_INTERVAL_HOURS, decision["interval_hours"] + 2)
                reasons.append(f"💧 high ambient humidity ({avg_humidity:.0f}%)")

        # Light (lux) adjustment — thresholds scaled by seasonal factor
        avg_light = sensor_data.get("avg_light")
        if avg_light is not None:
            sf = seasonal_light_factor()
            very_bright = LIGHT_VERY_BRIGHT * sf
            bright = LIGHT_BRIGHT * sf
            dark = LIGHT_DARK * sf
            very_dark = LIGHT_VERY_DARK * sf
            if avg_light > very_bright:
                # Seasonally very bright → high transpiration → more frequent
                decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 2)
                decision["duration_minutes"] = min(MAX_DURATION_MINUTES, decision["duration_minutes"] + 1)
                reasons.append(f"☀️ very bright ({avg_light:.0f} lux, seasonal)")
            elif avg_light > bright:
                decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 1)
                reasons.append(f"🌤️ bright ({avg_light:.0f} lux, seasonal)")
            elif avg_light < very_dark:
                # Seasonally very dark → minimal transpiration → less frequent
                decision["interval_hours"] = min(MAX_INTERVAL_HOURS, decision["interval_hours"] + 4)
                reasons.append(f"🌑 very low light ({avg_light:.0f} lux, seasonal)")
            elif avg_light < dark:
                decision["interval_hours"] = min(MAX_INTERVAL_HOURS, decision["interval_hours"] + 2)
                reasons.append(f"☁️ low light ({avg_light:.0f} lux, seasonal)")

        # Water needs adjustment
        if water_needs_level == "high":
            decision["duration_minutes"] = max(DEFAULT_DURATION_MINUTES, decision["duration_minutes"] + 1)
            decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 2)
        elif water_needs_level == "low":
            decision["duration_minutes"] = max(CONFLICT_DURATION_MINUTES, decision["duration_minutes"] - 1)
            decision["interval_hours"] = min(MAX_INTERVAL_HOURS, decision["interval_hours"] + 4)

        # Apply trend-based adjustments
        if trends.get("soil_moisture_trend") == "declining":
            decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 2)
            reasons.append("📉 soil moisture declining")
        elif trends.get("soil_moisture_trend") == "rising":
            decision["interval_hours"] = min(MAX_INTERVAL_HOURS, decision["interval_hours"] + 2)
            reasons.append("📈 soil moisture rising")

        if trends.get("temperature_trend") == "rising" and avg_temp and avg_temp > 25:
            decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 2)
            reasons.append("🌡️ temperature rising + hot")

        if trends.get("irrigation_frequency_low"):
            decision["duration_minutes"] = min(MAX_DURATION_MINUTES, decision["duration_minutes"] + 1)
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
        all_env_humidity = []
        all_soil = []
        all_light = []
        water_warnings = []
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
                if r.env_humidity is not None:
                    all_env_humidity.append(r.env_humidity)
                    s_humidity.append(r.env_humidity)
                if r.soil_moisture is not None:
                    all_soil.append(r.soil_moisture)
                    s_soil.append(r.soil_moisture)
                if r.light is not None and r.light > 15:  # exclude night readings
                    all_light.append(r.light)
                if r.water_warning:
                    water_warnings.append(sensor.name)

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
        if all_env_humidity:
            data["avg_env_humidity"] = statistics.mean(all_env_humidity)
        if water_warnings:
            data["water_warnings"] = list(set(water_warnings))
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
            return (DEFAULT_SOIL_MOISTURE_MIN, DEFAULT_SOIL_MOISTURE_MAX)

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
        - water_warning:     sensor's own soil-dry alert (high confidence)
        - water_stress:      soil moisture consistently low + declining trend
        - heat_stress:       prolonged high temperature
        - over_watering:     soil too wet + high irrigation frequency
        - low_env_humidity:  very dry air → high transpiration
        - low_light:         insufficient lux for plant type → reduced growth/transpiration
        """
        stress = {}

        # Device water_warning: sensor's own dry-soil detection (high confidence signal)
        water_warnings = sensor_data.get("water_warnings", [])
        if water_warnings:
            stress["water_warning"] = f"device alert on: {', '.join(water_warnings)}"

        # Env humidity stress: sustained very dry air for plant type
        avg_env_hum = sensor_data.get("avg_env_humidity")
        plants_for_stress = self.db.get_plants_in_cluster(cluster_id)
        if avg_env_hum is not None and plants_for_stress:
            plant_care_data_s = [
                self.plant_db.get_care_data(species=p.species, category=p.category) for p in plants_for_stress
            ]
            hum_range = self._get_ideal_humidity_range(plant_care_data_s)
            if hum_range and avg_env_hum < hum_range[0] - 20:
                stress["low_env_humidity"] = (
                    f"very dry air ({avg_env_hum:.0f}% vs ideal ≥{hum_range[0]:.0f}%) — high transpiration"
                )

        # Light stress: sustained low light for plants that need it (seasonal threshold)
        avg_light_s = sensor_data.get("avg_light")
        if avg_light_s is not None and plants_for_stress:
            plant_care_data_s2 = [
                self.plant_db.get_care_data(species=p.species, category=p.category) for p in plants_for_stress
            ]
            min_lux_needed = max((d.get("ideal_light_lux_min", 0) for d in plant_care_data_s2), default=0)
            seasonal_min = effective_light_threshold(min_lux_needed)
            if min_lux_needed > 0 and avg_light_s < seasonal_min * 0.4:
                stress["low_light"] = (
                    f"insufficient light ({avg_light_s:.0f} lux vs seasonal min {seasonal_min:.0f}) — "
                    f"reduced transpiration and growth"
                )

        # Water stress: soil moisture consistently low + declining trend
        avg_soil = sensor_data.get("avg_soil_moisture")
        if avg_soil:
            # Critical: below SOIL_MOISTURE_CRITICAL or below SOIL_MOISTURE_LOW with steep decline
            if avg_soil < SOIL_MOISTURE_CRITICAL:
                stress["water_stress"] = f"critical low ({avg_soil:.0f}%)"
                if trends.get("soil_moisture_trend") == "declining":
                    stress["water_stress"] += " + declining"
            elif avg_soil < SOIL_MOISTURE_LOW and trends.get("soil_moisture_trend") == "declining":
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
        if avg_soil and avg_soil > SOIL_MOISTURE_SATURATED:
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
                    "duration_minutes": config.duration_minutes or DEFAULT_DURATION_MINUTES,
                    "interval_hours": config.interval_hours or DEFAULT_INTERVAL_HOURS,
                    "reason": "using configured schedule (no sensor data)",
                    "confidence": CONFIDENCE_CONFIG_FALLBACK,
                }
            return {
                "action": "skip",
                "duration_minutes": DEFAULT_DURATION_MINUTES,
                "interval_hours": DEFAULT_INTERVAL_HOURS,
                "reason": "insufficient data",
                "confidence": CONFIDENCE_NO_DATA,
            }

        # Temperature-based buckets
        if temp <= TEMP_COLD:
            interval = MAX_INTERVAL_HOURS
        elif temp <= TEMP_WARM:
            interval = DEFAULT_INTERVAL_HOURS
        elif temp <= TEMP_HOT:
            interval = CONFLICT_INTERVAL_HOURS
        else:
            interval = MIN_INTERVAL_HOURS

        # Adjust for water needs
        if water_needs == "high":
            interval = max(MIN_INTERVAL_HOURS, interval - 4)
        elif water_needs == "low":
            interval = min(MAX_INTERVAL_HOURS, interval + 6)

        # CRITICAL: Check last irrigation time to respect cooldown
        irrigators = self.db.get_irrigators_in_cluster(cluster_id)
        if irrigators:
            recent_events = self.db.get_recent_events(irrigators[0].id, hours=interval)
            irrigation_events = [e for e in recent_events if e.action in ("start", "schedule_updated")]
            if irrigation_events:
                # Found recent irrigation → skip
                return {
                    "action": "skip",
                    "duration_minutes": DEFAULT_DURATION_MINUTES,
                    "interval_hours": interval,
                    "reason": f"cooldown active (last irrigation < {interval}h ago)",
                    "confidence": CONFIDENCE_COOLDOWN,
                }

        # No recent irrigation → irrigate
        return {
            "action": "irrigate",
            "duration_minutes": DEFAULT_DURATION_MINUTES,
            "interval_hours": interval,
            "reason": f"temperature-based ({temp:.0f}°C, {water_needs} water needs, evidence-based data)",
            "confidence": CONFIDENCE_TEMP_FALLBACK,
        }
