"""Smart irrigation decision engine using evidence-based plant data.

The engine produces a typed :class:`IrrigationDecision` per evaluation.
The pipeline composes pure rule functions so each step is independently
testable and contributes structured ``Reason`` entries to the trail.
"""

import logging
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
from tuya_irrigation_core.logic.decision import (
    Action,
    IrrigationDecision,
    Reason,
    SensorSnapshot,
    Severity,
    StressIndicators,
    Trends,
    TriggerCode,
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

log = logging.getLogger(__name__)


class IrrigationLogic:
    """Smart irrigation decision engine using evidence-based plant data."""

    def __init__(self, db: IrrigationRepository, plant_db: PlantDatabase):
        self.db = db
        self.plant_db = plant_db

    def decide_for_cluster(
        self,
        cluster_id: int,
        current_temp: float | None = None,
        *,
        persist: bool = False,
        triggered_by: str = "auto",
    ) -> IrrigationDecision | None:
        """Run the rule pipeline for a cluster and return a typed decision.

        Returns ``None`` if the cluster does not exist; returns a decision
        with ``action == SKIP`` and an explanatory reason for empty
        clusters, cooldown windows, or insufficient data.
        """
        cluster = self.db.get_cluster(cluster_id)
        if not cluster:
            return None

        evaluated_at = int(time.time())
        plants = self.db.get_plants_in_cluster(cluster_id)
        if not plants:
            decision = _decision_with_reason(
                cluster_id,
                evaluated_at,
                Action.SKIP,
                DEFAULT_DURATION_MINUTES,
                DEFAULT_INTERVAL_HOURS,
                confidence=0.0,
                code=TriggerCode.NO_PLANTS,
                message="no plants in cluster",
            )
            if persist:
                self._persist(decision, triggered_by)
            return decision

        cooldown = self._enforce_cooldown(cluster_id, evaluated_at)
        if cooldown is not None:
            if persist:
                self._persist(cooldown, triggered_by)
            return cooldown

        snapshot = get_recent_sensor_data(self.db, cluster_id, hours=24)
        trends = analyze_historical_trends(self.db, cluster_id)
        stress = detect_stress_conditions(self.db, self.plant_db, cluster_id, snapshot, trends)
        self._attach_learning_alerts(cluster_id, stress)

        plant_care = [self.plant_db.get_care_data(species=p.species, category=p.category) for p in plants]
        ideal_temp_range = get_ideal_temp_range(plant_care)
        ideal_humidity_range = get_ideal_humidity_range(plant_care)
        water_needs = analyze_water_needs(plant_care)

        sensors = self.db.get_sensors_in_cluster(cluster_id)
        if not sensors or not snapshot.has_data:
            fallback = temperature_based_decision(
                self.db,
                cluster_id,
                evaluated_at,
                temp=current_temp,
                water_needs=water_needs,
                temp_range=ideal_temp_range,
                config=self.db.get_irrigation_config(cluster_id),
                trends=trends,
                stress=stress,
            )
            if persist:
                self._persist(fallback, triggered_by)
            return fallback

        decision = IrrigationDecision(
            cluster_id=cluster_id,
            evaluated_at=evaluated_at,
            action=Action.SKIP,
            duration_minutes=DEFAULT_DURATION_MINUTES,
            interval_hours=DEFAULT_INTERVAL_HOURS,
            confidence=0.5,
            sensor_snapshot=snapshot,
            stress_indicators=stress,
            trends=trends,
        )

        if _apply_water_warning_rule(decision):
            return decision
        if _apply_critical_stress_rule(decision):
            return decision

        _apply_soil_moisture_rule(decision, plant_care)
        _apply_temperature_adjustment(decision, ideal_temp_range)
        _apply_humidity_adjustment(decision, ideal_humidity_range)
        _apply_light_adjustment(decision)
        _apply_water_needs_adjustment(decision, water_needs)
        _apply_trend_adjustment(decision)

        if persist:
            self._persist(decision, triggered_by)
        return decision

    def _persist(self, decision: IrrigationDecision, triggered_by: str) -> None:
        """Best-effort persistence — never blocks the decision."""
        try:
            payload = decision.model_dump(mode="json")
            self.db.add_decision_log(
                cluster_id=decision.cluster_id,
                evaluated_at=decision.evaluated_at,
                action=decision.action.value,
                duration_minutes=decision.duration_minutes,
                interval_hours=decision.interval_hours,
                confidence=decision.confidence,
                primary_code=decision.primary_code.value if decision.primary_code else None,
                reason_text=decision.reason_text,
                payload=payload,
                triggered_by=triggered_by,
                actuated=False,
            )
        except Exception:
            log.warning("failed to persist decision log", exc_info=True)

    def _enforce_cooldown(self, cluster_id: int, now: int) -> IrrigationDecision | None:
        """Skip when ANY irrigator in the cluster fired within the cooldown window."""
        irrigators = self.db.get_irrigators_in_cluster(cluster_id)
        if not irrigators:
            return None

        latest_event = None
        for irr in irrigators:
            events = self.db.get_recent_events(irr.id, hours=MIN_COOLDOWN_HOURS)
            for event in events:
                if event.action != "start":
                    continue
                if latest_event is None or event.timestamp > latest_event.timestamp:
                    latest_event = event

        if latest_event is None:
            return None

        hours_ago = (now - latest_event.timestamp) / 3600
        return _decision_with_reason(
            cluster_id,
            now,
            Action.SKIP,
            DEFAULT_DURATION_MINUTES,
            MIN_COOLDOWN_HOURS,
            confidence=CONFIDENCE_COOLDOWN,
            code=TriggerCode.COOLDOWN,
            message=f"cooldown active (last irrigation {hours_ago:.1f}h ago, trigger: {latest_event.triggered_by})",
        )

    def _attach_learning_alerts(self, cluster_id: int, stress: StressIndicators) -> None:
        """Best-effort learning alert collection — never blocks the decision."""
        try:
            from tuya_irrigation_core.learning import IrrigationLearner

            learner = IrrigationLearner(self.db, self.plant_db)
            alerts = learner.detect_issues(cluster_id)
            if alerts:
                stress.learning_alerts = [
                    {"type": a.alert_type, "severity": a.severity, "message": a.message} for a in alerts
                ]
        except Exception:
            log.warning("learning advisory unavailable", exc_info=True)


# ── Rule functions ──────────────────────────────────────────────────────────


def _decision_with_reason(
    cluster_id: int,
    evaluated_at: int,
    action: Action,
    duration_minutes: int,
    interval_hours: int,
    *,
    confidence: float,
    code: TriggerCode,
    message: str,
    severity: Severity = Severity.INFO,
    sensor_snapshot: SensorSnapshot | None = None,
    stress_indicators: StressIndicators | None = None,
    trends: Trends | None = None,
) -> IrrigationDecision:
    """Build a one-reason decision (used by terminal rules)."""
    decision = IrrigationDecision(
        cluster_id=cluster_id,
        evaluated_at=evaluated_at,
        action=action,
        duration_minutes=duration_minutes,
        interval_hours=interval_hours,
        confidence=confidence,
        sensor_snapshot=sensor_snapshot,
        stress_indicators=stress_indicators or StressIndicators(),
        trends=trends or Trends(),
    )
    decision.add_reason(code=code, message=message, severity=severity)
    return decision


def _apply_water_warning_rule(decision: IrrigationDecision) -> bool:
    """Sensor's own water_warning is a high-confidence terminal trigger."""
    warning = decision.stress_indicators.water_warning
    if not warning:
        return False
    decision.action = Action.IRRIGATE
    decision.duration_minutes = STRESS_DURATION_MINUTES
    decision.interval_hours = STRESS_INTERVAL_HOURS
    decision.confidence = CONFIDENCE_WATER_WARNING
    decision.add_reason(
        code=TriggerCode.WATER_WARNING,
        message=f"sensor alert: {warning}",
        severity=Severity.CRITICAL,
        icon="warning-circle",
    )
    return True


def _apply_critical_stress_rule(decision: IrrigationDecision) -> bool:
    """Critical stress (water_stress, over_watering) is a terminal trigger."""
    stress = decision.stress_indicators
    if stress.water_stress:
        decision.action = Action.IRRIGATE
        decision.duration_minutes = STRESS_DURATION_MINUTES
        decision.interval_hours = STRESS_INTERVAL_HOURS
        decision.confidence = CONFIDENCE_CRITICAL_STRESS
        decision.add_reason(
            code=TriggerCode.WATER_STRESS,
            message=f"water stress detected ({stress.water_stress})",
            severity=Severity.CRITICAL,
            icon="drop",
        )
        return True
    if stress.over_watering:
        decision.action = Action.SKIP
        decision.interval_hours = MAX_INTERVAL_HOURS
        decision.confidence = CONFIDENCE_OVER_WATERING
        decision.add_reason(
            code=TriggerCode.OVER_WATERING,
            message=f"over-watering detected ({stress.over_watering})",
            severity=Severity.WARNING,
            icon="drop-half",
        )
        return True
    return False


def _apply_soil_moisture_rule(decision: IrrigationDecision, plant_care: list[dict]) -> None:
    """Min-soil-moisture rule with conflict detection (driest plant drives it)."""
    snapshot = decision.sensor_snapshot
    if snapshot is None or snapshot.avg_soil_moisture is None:
        return

    target_ranges = [parse_moisture_target(d.get("soil_moisture_target", "45-65")) for d in plant_care]
    target_min = min(r[0] for r in target_ranges)
    target_max = max(r[1] for r in target_ranges)

    min_soil = snapshot.min_soil_moisture if snapshot.min_soil_moisture is not None else snapshot.avg_soil_moisture
    max_soil = snapshot.max_soil_moisture if snapshot.max_soil_moisture is not None else snapshot.avg_soil_moisture

    has_conflict = (min_soil < target_min) and (max_soil > target_max - 10)
    if has_conflict:
        dry_names = [s.name for s in snapshot.per_sensor if s.avg_soil_moisture and s.avg_soil_moisture < target_min]
        wet_names = [
            s.name for s in snapshot.per_sensor if s.avg_soil_moisture and s.avg_soil_moisture > target_max - 10
        ]
        decision.action = Action.IRRIGATE
        decision.duration_minutes = CONFLICT_DURATION_MINUTES
        decision.interval_hours = CONFLICT_INTERVAL_HOURS
        decision.confidence = CONFIDENCE_CONFLICT
        decision.add_reason(
            code=TriggerCode.CONFLICT,
            message=(
                f"conflict: dry={min_soil:.0f}% ({', '.join(dry_names) or '?'}), "
                f"wet={max_soil:.0f}% ({', '.join(wet_names) or '?'}) — short burst"
            ),
            severity=Severity.WARNING,
            icon="scales",
        )
        return

    if min_soil < target_min - 10:
        decision.action = Action.IRRIGATE
        decision.duration_minutes = STRESS_DURATION_MINUTES
        decision.interval_hours = CONFLICT_INTERVAL_HOURS
        decision.confidence = CONFIDENCE_SENSOR_VERY_DRY
        decision.add_reason(
            code=TriggerCode.SENSOR_VERY_DRY,
            message=f"soil very dry (driest={min_soil:.0f}% < {target_min}%)",
            severity=Severity.WARNING,
            icon="drop",
        )
    elif min_soil < target_min:
        decision.action = Action.IRRIGATE
        decision.duration_minutes = DEFAULT_DURATION_MINUTES
        decision.interval_hours = DEFAULT_INTERVAL_HOURS
        decision.confidence = CONFIDENCE_SENSOR_DRY
        decision.add_reason(
            code=TriggerCode.SENSOR_DRY,
            message=f"soil moderately dry (driest={min_soil:.0f}%)",
            icon="drop",
        )
    elif snapshot.avg_soil_moisture <= target_max:
        decision.action = Action.SKIP
        decision.confidence = CONFIDENCE_SENSOR_ADEQUATE
        decision.add_reason(
            code=TriggerCode.SENSOR_ADEQUATE,
            message=f"soil moisture adequate (range={min_soil:.0f}-{max_soil:.0f}%)",
            icon="check-circle",
        )
    else:
        decision.action = Action.SKIP
        decision.confidence = CONFIDENCE_SENSOR_WET
        decision.add_reason(
            code=TriggerCode.SENSOR_WET,
            message=f"soil too wet (wettest={max_soil:.0f}% > {target_max}%)",
            icon="drop-half",
        )


def _apply_temperature_adjustment(decision: IrrigationDecision, temp_range: tuple[float, float] | None) -> None:
    avg_temp = decision.sensor_snapshot.avg_temperature if decision.sensor_snapshot else None
    if avg_temp is None or not temp_range:
        return
    if avg_temp > temp_range[1] + 3:
        delta = -(decision.interval_hours - max(MIN_INTERVAL_HOURS, decision.interval_hours - 4))
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - 4)
        decision.add_reason(
            code=TriggerCode.TEMP_HIGH,
            message=f"temp above ideal ({avg_temp:.0f}°C > {temp_range[1]:.0f}°C)",
            icon="thermometer-hot",
            interval_delta=delta,
        )
    elif avg_temp < temp_range[0] - 3:
        delta = min(MAX_INTERVAL_HOURS, decision.interval_hours + 6) - decision.interval_hours
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + 6)
        decision.add_reason(
            code=TriggerCode.TEMP_LOW,
            message=f"temp below ideal ({avg_temp:.0f}°C < {temp_range[0]:.0f}°C)",
            icon="thermometer-cold",
            interval_delta=delta,
        )


def _apply_humidity_adjustment(
    decision: IrrigationDecision, humidity_range: tuple[float, float] | None
) -> None:
    avg_hum = decision.sensor_snapshot.avg_env_humidity if decision.sensor_snapshot else None
    if avg_hum is None or not humidity_range:
        return
    if avg_hum < humidity_range[0] - 20:
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - 3)
        decision.add_reason(
            code=TriggerCode.HUMIDITY_VERY_LOW,
            message=f"very dry air ({avg_hum:.0f}% << ideal {humidity_range[0]:.0f}%)",
            icon="wind",
            interval_delta=-3,
        )
    elif avg_hum < humidity_range[0] - 5:
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - 1)
        decision.add_reason(
            code=TriggerCode.HUMIDITY_LOW,
            message=f"dry air ({avg_hum:.0f}%)",
            icon="wind",
            interval_delta=-1,
        )
    elif avg_hum > humidity_range[1] + 10:
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + 2)
        decision.add_reason(
            code=TriggerCode.HUMIDITY_HIGH,
            message=f"high ambient humidity ({avg_hum:.0f}%)",
            icon="cloud-rain",
            interval_delta=2,
        )


def _apply_light_adjustment(decision: IrrigationDecision) -> None:
    avg_light = decision.sensor_snapshot.avg_light if decision.sensor_snapshot else None
    if avg_light is None:
        return
    sf = seasonal_light_factor()
    if avg_light > LIGHT_VERY_BRIGHT * sf:
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - 2)
        decision.duration_minutes = min(MAX_DURATION_MINUTES, decision.duration_minutes + 1)
        decision.add_reason(
            code=TriggerCode.LIGHT_VERY_BRIGHT,
            message=f"very bright ({avg_light:.0f} lux, seasonal)",
            icon="sun",
            interval_delta=-2,
            duration_delta=1,
        )
    elif avg_light > LIGHT_BRIGHT * sf:
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - 1)
        decision.add_reason(
            code=TriggerCode.LIGHT_BRIGHT,
            message=f"bright ({avg_light:.0f} lux, seasonal)",
            icon="sun-dim",
            interval_delta=-1,
        )
    elif avg_light < LIGHT_VERY_DARK * sf:
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + 4)
        decision.add_reason(
            code=TriggerCode.LIGHT_VERY_DARK,
            message=f"very low light ({avg_light:.0f} lux, seasonal)",
            icon="moon",
            interval_delta=4,
        )
    elif avg_light < LIGHT_DARK * sf:
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + 2)
        decision.add_reason(
            code=TriggerCode.LIGHT_DARK,
            message=f"low light ({avg_light:.0f} lux, seasonal)",
            icon="cloud",
            interval_delta=2,
        )


def _apply_water_needs_adjustment(decision: IrrigationDecision, water_needs: str) -> None:
    if water_needs == "high":
        decision.duration_minutes = max(DEFAULT_DURATION_MINUTES, decision.duration_minutes + 1)
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - 2)
    elif water_needs == "low":
        decision.duration_minutes = max(CONFLICT_DURATION_MINUTES, decision.duration_minutes - 1)
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + 4)


def _apply_trend_adjustment(decision: IrrigationDecision) -> None:
    trends = decision.trends
    snapshot = decision.sensor_snapshot
    if trends.soil_moisture_trend == "declining":
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - 2)
        decision.add_reason(
            code=TriggerCode.TREND_MOISTURE_DECLINING,
            message="soil moisture declining",
            icon="trend-down",
            interval_delta=-2,
        )
    elif trends.soil_moisture_trend == "rising":
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + 2)
        decision.add_reason(
            code=TriggerCode.TREND_MOISTURE_RISING,
            message="soil moisture rising",
            icon="trend-up",
            interval_delta=2,
        )

    avg_temp = snapshot.avg_temperature if snapshot else None
    if trends.temperature_trend == "rising" and avg_temp and avg_temp > 25:
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - 2)
        decision.add_reason(
            code=TriggerCode.TREND_TEMP_RISING,
            message="temperature rising + hot",
            icon="thermometer-hot",
            interval_delta=-2,
        )

    if trends.irrigation_frequency_low:
        decision.duration_minutes = min(MAX_DURATION_MINUTES, decision.duration_minutes + 1)
        decision.add_reason(
            code=TriggerCode.UNDERWATERING_PATTERN,
            message="recent under-watering pattern",
            icon="chart-line-down",
            duration_delta=1,
        )


__all__ = ["IrrigationLogic", "Reason"]
