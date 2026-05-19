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

    irrigators = db.get_irrigators_in_cluster(cluster_id)
    latest_event = None
    for irr in irrigators:
        events = db.get_recent_events(irr.id, hours=interval)
        for event in events:
            if event.action not in ("start", "schedule_updated"):
                continue
            if latest_event is None or event.timestamp > latest_event.timestamp:
                latest_event = event

    if latest_event is not None:
        base.action = Action.SKIP
        base.duration_minutes = DEFAULT_DURATION_MINUTES
        base.interval_hours = interval
        base.confidence = CONFIDENCE_TEMP_FALLBACK
        base.add_reason(
            code=TriggerCode.COOLDOWN,
            message=f"cooldown active (last irrigation < {interval}h ago)",
            severity=Severity.INFO,
            icon="hourglass",
        )
        return base

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
