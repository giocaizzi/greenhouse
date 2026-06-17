"""Temperature-based fallback when the cluster has no live sensor data."""

from greenhouse_core.constants import (
    CONFIDENCE_CONFIG_FALLBACK,
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
from greenhouse_core.logic.decision import (
    Action,
    IrrigationDecision,
    Severity,
    StressIndicators,
    Trends,
    TriggerCode,
)
from greenhouse_core.models import IrrigationConfig
from greenhouse_core.repository import IrrigationRepository


def temperature_based_decision(
    db: IrrigationRepository,
    cluster_id: int,
    evaluated_at: int,
    *,
    temp: float | None,
    water_needs: str,
    temp_range: tuple[float, float] | None,
    config: IrrigationConfig | None,
    trends: Trends | None = None,
    stress: StressIndicators | None = None,
) -> IrrigationDecision:
    """Build a typed decision when there is no usable sensor data."""
    base = IrrigationDecision(
        cluster_id=cluster_id,
        evaluated_at=evaluated_at,
        action=Action.SKIP,
        duration_minutes=DEFAULT_DURATION_MINUTES,
        interval_hours=DEFAULT_INTERVAL_HOURS,
        confidence=CONFIDENCE_NO_DATA,
        stress_indicators=stress or StressIndicators(),
        trends=trends or Trends(),
    )

    if temp is None:
        if config:
            # Honor the *effective* mode/duration/interval so a global-only
            # override (cluster row leaves them null) still drives the
            # fallback. Local raw config keeps the row alive but defers to
            # global defaults via the resolver.
            effective = db.get_effective_config(cluster_id)
            effective_mode = effective["mode"]["value"]
            base.action = Action.SKIP if effective_mode == "manual" else Action.IRRIGATE
            base.duration_minutes = int(effective["duration_minutes"]["value"] or DEFAULT_DURATION_MINUTES)
            base.interval_hours = int(effective["interval_hours"]["value"] or DEFAULT_INTERVAL_HOURS)
            base.confidence = CONFIDENCE_CONFIG_FALLBACK
            base.add_reason(
                code=TriggerCode.CONFIG_FALLBACK,
                message="using configured schedule (no sensor data)",
                severity=Severity.WARNING,
                icon="gear",
            )
            return base
        base.add_reason(
            code=TriggerCode.NO_DATA,
            message="insufficient data",
            severity=Severity.WARNING,
            icon="question",
        )
        return base

    if temp <= TEMP_COLD:
        interval = MAX_INTERVAL_HOURS
    elif temp <= TEMP_WARM:
        interval = DEFAULT_INTERVAL_HOURS
    elif temp <= TEMP_HOT:
        interval = CONFLICT_INTERVAL_HOURS
    else:
        interval = MIN_INTERVAL_HOURS

    if water_needs == "high":
        interval = max(MIN_INTERVAL_HOURS, interval - 4)
    elif water_needs == "low":
        interval = min(MAX_INTERVAL_HOURS, interval + 6)

    # Cooldown is NOT re-checked here: the engine runs `_enforce_cooldown`
    # (single source of truth — `start` events over a fixed 6h window) before
    # ever reaching this fallback, so a duplicate gate over a variable window
    # that also counted `schedule_updated` only diverged from it. This path is
    # only reached when no cooldown applies.
    base.action = Action.IRRIGATE
    base.duration_minutes = DEFAULT_DURATION_MINUTES
    base.interval_hours = interval
    base.confidence = CONFIDENCE_TEMP_FALLBACK
    base.add_reason(
        code=TriggerCode.TEMP_FALLBACK,
        message=f"temperature-based ({temp:.0f}°C, {water_needs} water needs)",
        icon="thermometer",
    )
    return base
