"""Issue detection and alert generation from learned irrigation data."""

import statistics

from tuya_irrigation_core.constants import (
    LEARNING_MIN_ABSORPTION_PER_MIN,
    LEARNING_MIN_EFFICIENCY,
    LEARNING_MIN_EVENTS,
    LEARNING_OVER_WATER_THRESHOLD,
    LEARNING_RAPID_DRAINAGE_THRESHOLD,
    LIGHT_BRIGHT,
)
from tuya_irrigation_core.learning.models import Alert
from tuya_irrigation_core.learning.profiling import get_plant_profile
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_core.utils import daytime_lux_readings, effective_light_threshold, seasonal_light_factor


def detect_issues(
    db: IrrigationRepository,
    plant_db: PlantDatabase,
    cluster_id: int,
) -> list[Alert]:
    """Detect efficiency issues and unresolvable conflicts.

    Alert types:
    - blocked_drip:               sensor shows no moisture change after irrigation
    - rapid_drainage:             plant dries abnormally fast (soil retention issue)
    - light_accelerated_drainage: rapid drainage correlated with high lux (transpiration)
    - chronic_underwatering:      plant never reaches target moisture after irrigation
    - unresolvable_conflict:      single irrigator can't satisfy all plants
    - low_light:                  avg lux below plant's minimum need (7d avg)
    - low_env_humidity:           ambient humidity below ideal for plant type (48h avg)
    """
    alerts = []
    sensors = db.get_sensors_in_cluster(cluster_id)
    if not sensors:
        return alerts

    profiles = {}
    for sensor in sensors:
        profile = get_plant_profile(db, sensor)
        if profile:
            profiles[sensor.id] = profile

    if not profiles:
        return alerts  # Not enough data yet

    plants = db.get_plants_in_cluster(cluster_id)
    plant_care = {p.id: plant_db.get_care_data(species=p.species, category=p.category) for p in plants}

    for sensor in sensors:
        profile = profiles.get(sensor.id)
        if not profile or profile.response_count < LEARNING_MIN_EVENTS:
            continue  # Not enough data

        # 1. Blocked drip: consistently low response
        if (
            profile.efficiency_score < LEARNING_MIN_EFFICIENCY
            and profile.avg_absorption_per_minute < LEARNING_MIN_ABSORPTION_PER_MIN
        ):
            alerts.append(
                Alert(
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
                )
            )

        # 2. Rapid drainage — with light correlation
        if profile.avg_drainage_per_hour < LEARNING_RAPID_DRAINAGE_THRESHOLD:
            # Check if high light explains the drainage (daytime readings only)
            recent_readings = db.get_recent_readings(sensor.id, hours=48)
            avg_lux = None
            lux_readings = daytime_lux_readings(recent_readings)
            if lux_readings:
                avg_lux = statistics.mean(lux_readings)

            bright_threshold = LIGHT_BRIGHT * seasonal_light_factor()
            if avg_lux is not None and avg_lux > bright_threshold:
                alerts.append(
                    Alert(
                        severity="warning",
                        alert_type="light_accelerated_drainage",
                        message=(
                            f"☀️💨 {sensor.name}: rapid drainage "
                            f"({profile.avg_drainage_per_hour:.1f}%/hr) correlated with high light "
                            f"({avg_lux:.0f} lux) — increased transpiration. "
                            f"Consider more frequent irrigation on bright days."
                        ),
                        sensor_name=sensor.name,
                        data={"drainage_rate": profile.avg_drainage_per_hour, "avg_lux": avg_lux},
                    )
                )
            else:
                alerts.append(
                    Alert(
                        severity="warning",
                        alert_type="rapid_drainage",
                        message=(
                            f"💨 {sensor.name}: rapid drainage "
                            f"({profile.avg_drainage_per_hour:.1f}%/hr). "
                            f"Soil may not retain water well."
                        ),
                        sensor_name=sensor.name,
                        data={"drainage_rate": profile.avg_drainage_per_hour},
                    )
                )

        # 3. Chronic underwatering: max delta never reaches target
        if sensor.plant_id and sensor.plant_id in plant_care:
            care = plant_care[sensor.plant_id]
            target = care.get("soil_moisture_target", "45-65")
            try:
                target_min = float(target.split("-")[0])
            except (ValueError, IndexError):
                target_min = 45.0

            # Check if recent readings ever reach target
            recent = db.get_recent_readings(sensor.id, hours=168)  # 7 days
            if recent:
                max_recent = max((r.soil_moisture for r in recent if r.soil_moisture is not None), default=0)
                if max_recent < target_min and profile.response_count >= 5:
                    alerts.append(
                        Alert(
                            severity="warning",
                            alert_type="chronic_underwatering",
                            message=(
                                f"🏜️ {sensor.name}: soil never reaches target "
                                f"({max_recent:.0f}% peak vs {target_min:.0f}% target). "
                                f"Consider longer irrigation or check drip flow."
                            ),
                            sensor_name=sensor.name,
                            data={"max_recent": max_recent, "target_min": target_min},
                        )
                    )

    # 4. Unresolvable conflict: check if profiles show incompatible needs
    if len(profiles) >= 2:
        alerts.extend(detect_conflicts(db, plant_db, cluster_id, profiles, plant_care))

    return alerts


def detect_conflicts(
    db: IrrigationRepository,
    plant_db: PlantDatabase,
    cluster_id: int,
    profiles: dict,
    plant_care: dict,
) -> list[Alert]:
    """Detect unresolvable conflicts between plants in same cluster."""
    alerts = []
    sensors = db.get_sensors_in_cluster(cluster_id)

    # Get current moisture levels
    sensor_moisture = {}
    for sensor in sensors:
        readings = db.get_recent_readings(sensor.id, hours=6)
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
            needed_delta = dry_target - dry_m
            needed_minutes = needed_delta / dry_profile.avg_absorption_per_minute

            for wet_s, wet_m, wet_target in wet_sensors:
                wet_profile = profiles.get(wet_s.id)
                if not wet_profile:
                    continue
                wet_gain = wet_profile.avg_absorption_per_minute * needed_minutes
                projected_wet = wet_m + wet_gain

                if projected_wet > LEARNING_OVER_WATER_THRESHOLD:
                    alerts.append(
                        Alert(
                            severity="critical",
                            alert_type="unresolvable_conflict",
                            message=(
                                f"⚠️ Unresolvable conflict: {dry_s.name} needs "
                                f"~{needed_minutes:.0f}min of irrigation ({dry_m:.0f}%→{dry_target:.0f}%), "
                                f"but {wet_s.name} would reach {projected_wet:.0f}% "
                                f"(current {wet_m:.0f}%, max {wet_target:.0f}%). "
                                f"Consider: repositioning drip, separate pot, or dedicated irrigator."
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
                        )
                    )

    # 4. Low light: plant gets insufficient lux for its needs
    all_plants = db.get_plants_in_cluster(cluster_id)
    plants_by_id = {p.id: p for p in all_plants}
    for sensor in sensors:
        plant = plants_by_id.get(sensor.plant_id) if sensor.plant_id else None
        if not plant:
            continue
        care = plant_db.get_care_data(species=plant.species, category=plant.category)
        min_lux = care.get("ideal_light_lux_min")
        if not min_lux:
            continue
        readings_7d = db.get_recent_readings(sensor.id, hours=168)
        lux_vals = daytime_lux_readings(readings_7d)  # exclude night readings
        if len(lux_vals) < 5:
            continue  # Not enough data
        avg_lux_7d = statistics.mean(lux_vals)
        seasonal_min_lux = effective_light_threshold(min_lux)  # adjusted for current month
        if avg_lux_7d < seasonal_min_lux * 0.5:
            alerts.append(
                Alert(
                    severity="warning",
                    alert_type="low_light",
                    message=(
                        f"🌑 {sensor.name} ({plant.species}): avg daytime light {avg_lux_7d:.0f} lux "
                        f"(7d avg, daytime only) — below seasonal minimum {seasonal_min_lux:.0f} lux "
                        f"(summer baseline {min_lux} lux). "
                        f"Move to brighter location or add grow light."
                    ),
                    sensor_name=sensor.name,
                    data={"avg_lux": avg_lux_7d, "min_lux": min_lux, "seasonal_min_lux": seasonal_min_lux},
                )
            )

    # 5. Low env humidity: sustained dry air for tropical plants
    for sensor in sensors:
        plant = plants_by_id.get(sensor.plant_id) if sensor.plant_id else None
        if not plant:
            continue
        care2 = plant_db.get_care_data(species=plant.species, category=plant.category)
        ideal_hum_min = care2.get("ideal_humidity_min")
        if not ideal_hum_min:
            continue
        readings_48h = db.get_recent_readings(sensor.id, hours=48)
        hum_vals = [r.env_humidity for r in readings_48h if r.env_humidity is not None]
        if len(hum_vals) < 5:
            continue
        avg_env_hum = statistics.mean(hum_vals)
        if avg_env_hum < ideal_hum_min - 15:
            alerts.append(
                Alert(
                    severity="warning",
                    alert_type="low_env_humidity",
                    message=(
                        f"💨 {sensor.name} ({plant.species}): avg ambient humidity {avg_env_hum:.0f}% "
                        f"— below ideal {ideal_hum_min:.0f}%. "
                        f"Increased transpiration; consider humidifier or grouping plants."
                    ),
                    sensor_name=sensor.name,
                    data={"avg_env_humidity": avg_env_hum, "ideal_min": ideal_hum_min},
                )
            )

    return alerts
