"""Smart irrigation decision engine using evidence-based plant data."""

import time

from tuya_irrigation_core.constants import (
    CONFIDENCE_CONFLICT,
    CONFIDENCE_COOLDOWN,
    CONFIDENCE_CRITICAL_STRESS,
    CONFIDENCE_OVER_WATERING,
    CONFIDENCE_SENSOR_ADEQUATE,
    CONFIDENCE_SENSOR_DRY,
    CONFIDENCE_SENSOR_VERY_DRY,
    CONFIDENCE_SENSOR_WET,
    CONFIDENCE_WATER_WARNING,
    CONFLICT_DURATION_MINUTES,
    CONFLICT_INTERVAL_HOURS,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_INTERVAL_HOURS,
    LIGHT_BRIGHT,
    LIGHT_DARK,
    LIGHT_VERY_BRIGHT,
    LIGHT_VERY_DARK,
    MAX_DURATION_MINUTES,
    MAX_INTERVAL_HOURS,
    MIN_COOLDOWN_HOURS,
    MIN_INTERVAL_HOURS,
    STRESS_DURATION_MINUTES,
    STRESS_INTERVAL_HOURS,
)
from tuya_irrigation_core.logic.fallback import temperature_based_decision
from tuya_irrigation_core.logic.plant_needs import (
    analyze_water_needs,
    get_ideal_humidity_range,
    get_ideal_temp_range,
    parse_moisture_target,
)
from tuya_irrigation_core.logic.sensors import get_recent_sensor_data
from tuya_irrigation_core.logic.stress import detect_stress_conditions
from tuya_irrigation_core.logic.trends import analyze_historical_trends
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_core.utils import seasonal_light_factor


class IrrigationLogic:
    """Smart irrigation decision engine using evidence-based plant data."""

    def __init__(self, db: IrrigationRepository, plant_db: PlantDatabase):
        self.db = db
        self.plant_db = plant_db

    def decide_for_cluster(self, cluster_id: int, current_temp: float | None = None) -> dict | None:
        """Decide if irrigation is needed for a cluster.

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
        sensor_data = get_recent_sensor_data(self.db, cluster_id, hours=24)

        # Analyze historical trends and stress conditions
        trends = analyze_historical_trends(self.db, cluster_id)
        stress = detect_stress_conditions(self.db, self.plant_db, cluster_id, sensor_data, trends)

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
        ideal_temp_range = get_ideal_temp_range(plant_care_data)
        ideal_humidity_range = get_ideal_humidity_range(plant_care_data)
        water_needs_level = analyze_water_needs(plant_care_data)

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
            return temperature_based_decision(
                self.db, current_temp, water_needs_level, ideal_temp_range, config, cluster_id
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
        if avg_soil_moisture is not None:
            target_ranges = [parse_moisture_target(d.get("soil_moisture_target", "45-65")) for d in plant_care_data]
            target_min = min(r[0] for r in target_ranges)
            target_max = max(r[1] for r in target_ranges)

            min_soil = sensor_data.get("min_soil_moisture", avg_soil_moisture)
            max_soil = sensor_data.get("max_soil_moisture", avg_soil_moisture)
            per_sensor = sensor_data.get("per_sensor", [])

            # Detect conflict: one plant dry, another already wet
            has_conflict = (min_soil < target_min) and (max_soil > target_max - 10)

            if has_conflict:
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
                decision["action"] = "irrigate"
                decision["duration_minutes"] = STRESS_DURATION_MINUTES
                decision["interval_hours"] = CONFLICT_INTERVAL_HOURS
                reasons.append(f"soil very dry (driest={min_soil:.0f}% < {target_min}%)")
                decision["confidence"] = CONFIDENCE_SENSOR_VERY_DRY
            elif min_soil < target_min:
                decision["action"] = "irrigate"
                decision["duration_minutes"] = DEFAULT_DURATION_MINUTES
                decision["interval_hours"] = DEFAULT_INTERVAL_HOURS
                reasons.append(f"soil moderately dry (driest={min_soil:.0f}%)")
                decision["confidence"] = CONFIDENCE_SENSOR_DRY
            elif avg_soil_moisture <= target_max:
                decision["action"] = "skip"
                reasons.append(f"soil moisture adequate (range={min_soil:.0f}-{max_soil:.0f}%)")
                decision["confidence"] = CONFIDENCE_SENSOR_ADEQUATE
            else:
                decision["action"] = "skip"
                reasons.append(f"soil too wet (wettest={max_soil:.0f}% > {target_max}%)")
                decision["confidence"] = CONFIDENCE_SENSOR_WET

        # Temperature adjustment
        if avg_temp is not None and ideal_temp_range:
            if avg_temp > ideal_temp_range[1] + 3:
                decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 4)
                reasons.append(f"temp above ideal ({avg_temp:.0f}°C > {ideal_temp_range[1]:.0f}°C)")
            elif avg_temp < ideal_temp_range[0] - 3:
                decision["interval_hours"] = min(MAX_INTERVAL_HOURS, decision["interval_hours"] + 6)
                reasons.append(f"temp below ideal ({avg_temp:.0f}°C < {ideal_temp_range[0]:.0f}°C)")

        # Env humidity adjustment
        if avg_humidity is not None and ideal_humidity_range:
            if avg_humidity < ideal_humidity_range[0] - 20:
                decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 3)
                reasons.append(f"💨 very dry air ({avg_humidity:.0f}% << ideal {ideal_humidity_range[0]:.0f}%)")
            elif avg_humidity < ideal_humidity_range[0] - 5:
                decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 1)
                reasons.append(f"💨 dry air ({avg_humidity:.0f}%)")
            elif avg_humidity > ideal_humidity_range[1] + 10:
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
                decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 2)
                decision["duration_minutes"] = min(MAX_DURATION_MINUTES, decision["duration_minutes"] + 1)
                reasons.append(f"☀️ very bright ({avg_light:.0f} lux, seasonal)")
            elif avg_light > bright:
                decision["interval_hours"] = max(MIN_INTERVAL_HOURS, decision["interval_hours"] - 1)
                reasons.append(f"🌤️ bright ({avg_light:.0f} lux, seasonal)")
            elif avg_light < very_dark:
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
