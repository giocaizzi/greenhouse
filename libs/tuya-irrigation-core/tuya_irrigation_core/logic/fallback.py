"""Temperature-based fallback logic when no sensor data is available."""

from tuya_irrigation_core.constants import (
    CONFIDENCE_CONFIG_FALLBACK,
    CONFIDENCE_COOLDOWN,
    CONFIDENCE_NO_DATA,
    CONFIDENCE_TEMP_FALLBACK,
    CONFLICT_INTERVAL_HOURS,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_INTERVAL_HOURS,
    MAX_INTERVAL_HOURS,
    MIN_INTERVAL_HOURS,
    TEMP_COLD,
    TEMP_HOT,
    TEMP_WARM,
)
from tuya_irrigation_core.models import IrrigationConfig
from tuya_irrigation_core.repository import IrrigationRepository


def temperature_based_decision(
    db: IrrigationRepository,
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
    irrigators = db.get_irrigators_in_cluster(cluster_id)
    if irrigators:
        recent_events = db.get_recent_events(irrigators[0].id, hours=interval)
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
