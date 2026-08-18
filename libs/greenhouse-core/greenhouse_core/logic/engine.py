"""Smart irrigation decision engine using evidence-based plant data.

The engine produces a typed :class:`IrrigationDecision` per evaluation.
The pipeline composes pure rule functions so each step is independently
testable and contributes structured ``Reason`` entries to the trail.
"""

import logging
import math
import time

from greenhouse_core.constants import (
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
    CONFLICT_WET_MARGIN,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_INTERVAL_HOURS,
    HUMIDITY_HIGH_INTERVAL_STEP,
    HUMIDITY_HIGH_OFFSET,
    HUMIDITY_LOW_INTERVAL_STEP,
    HUMIDITY_LOW_OFFSET,
    HUMIDITY_VERY_LOW_INTERVAL_STEP,
    HUMIDITY_VERY_LOW_OFFSET,
    LEAK_ALERT_CODE,
    LEAK_HOLD_HOURS,
    LIGHT_BRIGHT,
    LIGHT_BRIGHT_INTERVAL_STEP,
    LIGHT_DARK,
    LIGHT_DARK_INTERVAL_STEP,
    LIGHT_VERY_BRIGHT,
    LIGHT_VERY_BRIGHT_DURATION_STEP,
    LIGHT_VERY_BRIGHT_INTERVAL_STEP,
    LIGHT_VERY_DARK,
    LIGHT_VERY_DARK_INTERVAL_STEP,
    MAX_DURATION_MINUTES,
    MAX_INTERVAL_HOURS,
    MIN_COOLDOWN_HOURS,
    MIN_INTERVAL_HOURS,
    STRESS_DURATION_MINUTES,
    STRESS_INTERVAL_HOURS,
    TEMP_ADJUST_OFFSET,
    TEMP_HIGH_INTERVAL_STEP,
    TEMP_LOW_INTERVAL_STEP,
    TREND_MOISTURE_INTERVAL_STEP,
    TREND_TEMP_RISING_HOT_C,
    TREND_TEMP_RISING_INTERVAL_STEP,
    TREND_UNDERWATERING_DURATION_STEP,
    VACATION_MIN_RUN_MINUTES,
    VACATION_RESERVOIR_USABLE_FRACTION,
    VERY_DRY_MARGIN,
    WATER_NEEDS_DURATION_STEP,
    WATER_NEEDS_HIGH_INTERVAL_STEP,
    WATER_NEEDS_LOW_INTERVAL_STEP,
)
from greenhouse_core.logic.decision import (
    Action,
    IrrigationDecision,
    Reason,
    SensorSnapshot,
    Severity,
    StressIndicators,
    Trends,
    TriggerCode,
    WeatherSnapshot,
)
from greenhouse_core.logic.fallback import temperature_based_decision
from greenhouse_core.logic.plant_needs import (
    analyze_water_needs,
    get_ideal_humidity_range,
    get_ideal_temp_range,
    parse_moisture_target,
)
from greenhouse_core.logic.sensors import get_recent_sensor_data
from greenhouse_core.logic.stress import detect_stress_conditions
from greenhouse_core.logic.timing import (
    is_within_irrigation_window,
    is_within_quiet_hours,
    season_for,
    seasonal_multiplier,
)
from greenhouse_core.logic.trends import analyze_historical_trends
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.utils import seasonal_light_factor

log = logging.getLogger(__name__)


class IrrigationLogic:
    """Smart irrigation decision engine using evidence-based plant data."""

    def __init__(self, db: IrrigationRepository, plant_db: PlantDatabase, *, weather_client=None):
        self.db = db
        self.plant_db = plant_db
        self._weather = weather_client

    def decide_for_cluster(
        self,
        cluster_id: int,
        current_temp: float | None = None,
        *,
        persist: bool = False,
        triggered_by: str = "auto",
        bypass_quiet_hours: bool = False,
    ) -> IrrigationDecision | None:
        """Run the rule pipeline for a cluster and return a typed decision.

        Returns ``None`` if the cluster does not exist; returns a decision
        with ``action == SKIP`` and an explanatory reason for empty
        clusters, cooldown windows, or insufficient data.

        ``bypass_quiet_hours`` is set by manual-trigger callers (force-flag
        on ``/irrigate``, UI confirm) so the engine still records the
        override in the decision trail but does not produce a SKIP.
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

        # Safety gate first: a confirmed leak / stuck valve outranks every other
        # reason to skip, and saying so plainly beats reporting a cooldown that
        # happens to also be active.
        leak_hold = self._enforce_leak_hold(cluster_id, evaluated_at)
        if leak_hold is not None:
            if persist:
                self._persist(leak_hold, triggered_by)
            return leak_hold

        cooldown = self._enforce_cooldown(cluster_id, evaluated_at)
        if cooldown is not None:
            if persist:
                self._persist(cooldown, triggered_by)
            return cooldown

        # Quiet hours run after cooldown (cooldown is the cheaper, more
        # decisive gate) and before the weather rule so the audit trail
        # reflects the highest-priority reason for skipping. Manual
        # triggers bypass the SKIP but still leave a warning Reason on the
        # final decision so the audit log records the override.
        quiet_window = self._resolve_quiet_window(cluster_id, evaluated_at)
        if quiet_window is not None and not bypass_quiet_hours:
            skip = _decision_with_reason(
                cluster_id,
                evaluated_at,
                Action.SKIP,
                DEFAULT_DURATION_MINUTES,
                DEFAULT_INTERVAL_HOURS,
                confidence=CONFIDENCE_COOLDOWN,
                code=TriggerCode.QUIET_HOURS,
                message=(f"quiet hours active ({quiet_window[0]:02d}:00–{quiet_window[1]:02d}:00 local)"),
                severity=Severity.INFO,
            )
            if persist:
                self._persist(skip, triggered_by)
            return skip

        def _finalize(decision: IrrigationDecision) -> IrrigationDecision:
            if quiet_window is not None and bypass_quiet_hours:
                decision.add_reason(
                    code=TriggerCode.MANUAL_OVERRIDE_QUIET_HOURS,
                    message=(
                        f"manual override of quiet hours ({quiet_window[0]:02d}:00–{quiet_window[1]:02d}:00 local)"
                    ),
                    severity=Severity.WARNING,
                )
            if persist:
                self._persist(decision, triggered_by)
            return decision

        weather_skip = self._apply_weather_skip_rule(cluster, cluster_id, evaluated_at)
        if weather_skip is not None:
            return _finalize(weather_skip)

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
            return _finalize(fallback)

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
            return _finalize(decision)
        if _apply_critical_stress_rule(decision):
            return _finalize(decision)

        # Cluster-level timing gate — checked AFTER stress overrides on purpose:
        # a wilting plant still gets water at 2am, a healthy one doesn't.
        window_skip = self._apply_window_rule(cluster, cluster_id, evaluated_at, decision)
        if window_skip is not None:
            return _finalize(window_skip)

        _apply_soil_moisture_rule(decision, plant_care)
        _apply_temperature_adjustment(decision, ideal_temp_range)
        _apply_humidity_adjustment(decision, ideal_humidity_range)
        _apply_light_adjustment(decision)
        _apply_water_needs_adjustment(decision, water_needs)
        _apply_trend_adjustment(decision)

        # Seasonal interval scaling — multiplies the engine-chosen cadence by a
        # plant-aware factor so winter intervals stretch and summer intervals
        # tighten. Cooldown remains the safety floor (see MIN_COOLDOWN_HOURS).
        self._apply_seasonal_multiplier(cluster, decision, plant_care, evaluated_at)

        # Vacation rationing — the LAST adjustment so it clamps the final dosage
        # against the reservoir burn-down envelope (appends VACATION_ACTIVE for
        # audit; trims to VACATION_RATIONING or flips to SKIP with
        # VACATION_BUDGET_EXHAUSTED).
        self._apply_vacation_budget(decision, cluster_id, evaluated_at)

        return _finalize(decision)

    def _resolve_quiet_window(self, cluster_id: int, evaluated_at: int) -> tuple[int, int] | None:
        """Return the effective quiet-hours window for a cluster if it is
        currently inside one, else ``None``.

        Resolves quiet-hour bounds via the hierarchical config (cluster →
        global → built-in default in :mod:`constants`) and tests the current
        local hour against the resolved window. Wrap-around windows
        (start > end) cross midnight; ``start == end`` at any level means
        quiet hours are disabled there.
        """
        effective = self.db.get_effective_config(cluster_id)
        start = effective["quiet_start_hour"]["value"]
        end = effective["quiet_end_hour"]["value"]
        prefs = self.db.get_preferences()
        tz_name = prefs.timezone if prefs else None
        if is_within_quiet_hours(
            start_hour=int(start) if start is not None else None,
            end_hour=int(end) if end is not None else None,
            now_unix=evaluated_at,
            tz_name=tz_name,
        ):
            return (int(start), int(end))
        return None

    def _apply_window_rule(self, cluster, cluster_id, evaluated_at, decision):
        """Return a SKIP decision when the current local time is outside the
        cluster's irrigation windows.

        A cluster with NO configured ``IrrigationWindow`` rows allows watering at
        any hour — night protection is the job of quiet hours, not this rule (see
        issue #83). ``preferred_water_hours_local`` is still surfaced as advisory
        plant data via ``plant_db`` but no longer gates actuation. Stress
        overrides have already returned early before reaching this rule.
        """
        windows = self.db.list_irrigation_windows(cluster_id)
        if not windows:
            return None

        prefs = self.db.get_preferences()
        tz_name = prefs.timezone if prefs else None
        if is_within_irrigation_window(windows, now_unix=evaluated_at, tz_name=tz_name):
            return None
        return _decision_with_reason(
            cluster_id,
            evaluated_at,
            Action.SKIP,
            DEFAULT_DURATION_MINUTES,
            DEFAULT_INTERVAL_HOURS,
            confidence=CONFIDENCE_COOLDOWN,
            code=TriggerCode.OUTSIDE_WINDOW,
            message="outside configured watering window",
        )

    def _apply_seasonal_multiplier(self, cluster, decision, plant_care, evaluated_at):
        """Scale ``decision.interval_hours`` by a seasonal multiplier and append
        a ``SEASONAL_HOLD`` / ``SEASONAL_BOOST`` reason when the multiplier is
        not 1.0. Clamps to [MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS].

        Layers (most → least specific): species-level
        ``season_frequency_multiplier{,_outdoor}`` from the plant DB, then the
        category-level value exposed under ``_category_defaults``, then the
        built-in default tables in :mod:`constants`. The ``_outdoor`` variant
        is used when ``cluster.environment == "outdoor"`` and present; when
        missing for outdoor we fall to the next layer rather than silently
        reading the indoor key (avoids surprising mixed-env scaling).
        """
        prefs = self.db.get_preferences()
        tz_name = prefs.timezone if prefs else None
        environment = cluster.environment or "indoor"
        season = season_for(evaluated_at, tz_name=tz_name)

        season_key = (
            "season_frequency_multiplier_outdoor" if environment == "outdoor" else "season_frequency_multiplier"
        )

        # Engine is per-cluster, so a single representative plant drives the
        # multiplier. ``plant_db.get_care_data`` merges species fields over
        # category defaults at the top level, while ``_category_defaults`` stays
        # available verbatim — so we can ask :func:`seasonal_multiplier` to
        # respect both layers' per-season fallback semantics.
        plant_override = None
        category_override = None
        for care in plant_care:
            data = care if isinstance(care, dict) else {}
            if plant_override is None:
                plant_override = data.get(season_key)
            if category_override is None:
                cat_defaults = data.get("_category_defaults") or {}
                category_override = cat_defaults.get(season_key)
            if plant_override is not None and category_override is not None:
                break

        multiplier = seasonal_multiplier(
            season,
            environment=environment,
            plant_override=plant_override,
            category_override=category_override,
        )
        if multiplier == 1.0:
            return
        # Multiplier is a frequency factor (water N× as often), not an interval
        # factor — so divide the baseline interval by it to get the new cadence.
        new_interval = int(round(decision.interval_hours / multiplier))
        new_interval = max(MIN_INTERVAL_HOURS, min(MAX_INTERVAL_HOURS, new_interval))
        decision.interval_hours = new_interval
        decision.reasons = (
            *decision.reasons,
            Reason(
                code=TriggerCode.SEASONAL_HOLD if multiplier < 1.0 else TriggerCode.SEASONAL_BOOST,
                message=f"{season} multiplier {multiplier:g}× for {environment} cluster",
                severity=Severity.INFO,
            ),
        )

    def _apply_vacation_budget(self, decision: IrrigationDecision, cluster_id: int, now: int) -> None:
        """Clamp the final dosage against the reservoir burn-down envelope.

        Runs as the engine's last adjustment while a :class:`VacationWindow` is
        active. Always appends a ``VACATION_ACTIVE`` reason (even on SKIP) so the
        audit log records the vacation status. Rationing tracks the cluster's
        irrigator (the one ``run_irrigation_pipeline`` drives); when it has both
        ``reservoir_l`` and
        ``flow_rate_l_per_min`` set, the tank is assumed full at the vacation
        start and consumption is summed from the ``start`` events since then. A
        linear daily budget (cumulative through the current vacation day) bounds
        how many minutes it may still run.

        - Within budget → unchanged.
        - Partial budget (>= ``VACATION_MIN_RUN_MINUTES``) → duration trimmed and
          a ``VACATION_RATIONING`` reason appended.
        - No meaningful budget → flipped to SKIP with ``VACATION_BUDGET_EXHAUSTED``.

        Mutates ``decision`` in place; no-ops (apart from VACATION_ACTIVE) when no
        vacation is active, no capacity is configured, or the decision is not an
        IRRIGATE.
        """
        vac = self.db.get_active_vacation(at=now)
        if vac is None:
            return

        decision.add_reason(
            code=TriggerCode.VACATION_ACTIVE,
            message=f"vacation active (returns in {max(0, math.ceil((vac.ends_at - now) / 86400))}d)",
            severity=Severity.INFO,
            icon="airplane",
        )

        # Ration against the irrigator the pipeline actually actuates. A cluster
        # is "irrigated by the same device", so the budget tracks that one tank.
        irr = self.db.get_irrigator_for_cluster(cluster_id)
        if irr is None:
            return
        if not (irr.reservoir_l and irr.flow_rate_l_per_min):
            return
        if decision.action is not Action.IRRIGATE:
            return

        # Vacation length in whole days (at least 1) and the 0-based index of the
        # day we are currently in; the cumulative allowance grows day by day.
        d_days = max(1, math.ceil((vac.ends_at - vac.starts_at) / 86400))
        day_index = math.floor((now - vac.starts_at) / 86400)

        usable_l = irr.reservoir_l * VACATION_RESERVOIR_USABLE_FRACTION
        daily_budget_l = usable_l / d_days
        allowed_cum_l = min(usable_l, daily_budget_l * (day_index + 1))
        spent_l = self.db.irrigator_consumption_liters(irr.id, since=vac.starts_at, until=now)
        headroom_l = max(0.0, allowed_cum_l - spent_l)
        binding_max_min = math.floor(headroom_l / irr.flow_rate_l_per_min)

        if binding_max_min >= decision.duration_minutes:
            return

        if binding_max_min >= VACATION_MIN_RUN_MINUTES:
            decision.duration_minutes = binding_max_min
            decision.add_reason(
                code=TriggerCode.VACATION_RATIONING,
                message=f"trimmed to {binding_max_min} min so reservoir lasts the vacation",
                severity=Severity.WARNING,
                icon="drop-half",
            )
            return

        decision.action = Action.SKIP
        decision.duration_minutes = 0
        decision.confidence = CONFIDENCE_COOLDOWN
        decision.add_reason(
            code=TriggerCode.VACATION_BUDGET_EXHAUSTED,
            message="vacation water budget exhausted this cycle — skipping to conserve reservoir",
            severity=Severity.WARNING,
            icon="drop-slash",
        )

    def _persist(self, decision: IrrigationDecision, triggered_by: str) -> None:
        """Best-effort persistence — never blocks the decision."""
        try:
            payload = decision.model_dump(mode="json")
            decision.decision_log_id = self.db.add_decision_log(
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

    def _enforce_leak_hold(self, cluster_id: int, now: int) -> IrrigationDecision | None:
        """Skip while a confirmed leak / stuck valve is unresolved on this cluster.

        The hold is derived from the alert inbox rather than a separate table:
        ``LeakDetectionService`` raises one critical ``leak_or_stuck_valve``
        alert per offending sensor, and this gate blocks automatic actuation
        for as long as that alert is unresolved and no older than
        ``LEAK_HOLD_HOURS``. Two ways out, both operator-visible: resolve the
        alert once the hardware is checked, or let the hold age out.

        Deliberately not bypassable by ``force`` — a stuck valve is a hardware
        fault like the device-health alarms, so the escape hatch is the direct
        irrigator start endpoint, not the cluster pipeline.

        Args:
            cluster_id: Cluster being evaluated.
            now: Evaluation timestamp (Unix seconds).

        Returns:
            A terminal SKIP decision carrying ``TriggerCode.LEAK_HOLD``, or
            ``None`` when no hold is active.
        """
        hold_seconds = LEAK_HOLD_HOURS * 3600
        alert = self.db.get_active_alert(LEAK_ALERT_CODE, cluster_id=cluster_id, since=now - hold_seconds)
        if alert is None:
            return None

        hours_left = max(0.0, (alert.last_seen_at + hold_seconds - now) / 3600)
        return _decision_with_reason(
            cluster_id,
            now,
            Action.SKIP,
            0,
            LEAK_HOLD_HOURS,
            confidence=CONFIDENCE_COOLDOWN,
            code=TriggerCode.LEAK_HOLD,
            message=(
                f"leak/stuck-valve hold active ({hours_left:.1f}h left) — {alert.message}; "
                f"resolve alert #{alert.id} to release"
            ),
            severity=Severity.CRITICAL,
        )

    def _enforce_cooldown(self, cluster_id: int, now: int) -> IrrigationDecision | None:
        """Skip when the cluster's irrigator fired within the cooldown window."""
        irr = self.db.get_irrigator_for_cluster(cluster_id)
        if irr is None:
            return None

        latest_event = None
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

    def _apply_weather_skip_rule(self, cluster, cluster_id: int, evaluated_at: int) -> IrrigationDecision | None:
        """Skip irrigation for outdoor clusters when significant rain is forecast.

        No-ops when weather_client is not configured or the cluster is indoor.
        """
        if self._weather is None or cluster.environment == "indoor":
            return None

        forecast = self._weather.get_forecast(hours=6)
        if forecast is None:
            return None

        precip = forecast.get("precipitation_mm", 0.0) or 0.0
        if precip <= 2.0:
            return None

        decision = _decision_with_reason(
            cluster_id,
            evaluated_at,
            Action.SKIP,
            DEFAULT_DURATION_MINUTES,
            MAX_INTERVAL_HOURS,
            confidence=CONFIDENCE_OVER_WATERING,
            code=TriggerCode.WEATHER_SKIP,
            message=f"rain forecast ({precip:.1f}mm in next 6h) — skipping to avoid over-watering",
            severity=Severity.WARNING,
        )
        decision.weather = WeatherSnapshot(
            precipitation_next_6h_mm=precip,
            source="open-meteo",
        )
        return decision

    def _attach_learning_alerts(self, cluster_id: int, stress: StressIndicators) -> None:
        """Best-effort learning alert collection — never blocks the decision."""
        try:
            from greenhouse_core.learning import IrrigationLearner

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

    has_conflict = (min_soil < target_min) and (max_soil > target_max - CONFLICT_WET_MARGIN)
    if has_conflict:
        dry_names = [
            s.name for s in snapshot.per_sensor if s.avg_soil_moisture is not None and s.avg_soil_moisture < target_min
        ]
        wet_names = [
            s.name
            for s in snapshot.per_sensor
            if s.avg_soil_moisture is not None and s.avg_soil_moisture > target_max - CONFLICT_WET_MARGIN
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

    if min_soil < target_min - VERY_DRY_MARGIN:
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
    if avg_temp > temp_range[1] + TEMP_ADJUST_OFFSET:
        delta = -(decision.interval_hours - max(MIN_INTERVAL_HOURS, decision.interval_hours - TEMP_HIGH_INTERVAL_STEP))
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - TEMP_HIGH_INTERVAL_STEP)
        decision.add_reason(
            code=TriggerCode.TEMP_HIGH,
            message=f"temp above ideal ({avg_temp:.0f}°C > {temp_range[1]:.0f}°C)",
            icon="thermometer-hot",
            interval_delta=delta,
        )
    elif avg_temp < temp_range[0] - TEMP_ADJUST_OFFSET:
        delta = min(MAX_INTERVAL_HOURS, decision.interval_hours + TEMP_LOW_INTERVAL_STEP) - decision.interval_hours
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + TEMP_LOW_INTERVAL_STEP)
        decision.add_reason(
            code=TriggerCode.TEMP_LOW,
            message=f"temp below ideal ({avg_temp:.0f}°C < {temp_range[0]:.0f}°C)",
            icon="thermometer-cold",
            interval_delta=delta,
        )


def _apply_humidity_adjustment(decision: IrrigationDecision, humidity_range: tuple[float, float] | None) -> None:
    avg_hum = decision.sensor_snapshot.avg_env_humidity if decision.sensor_snapshot else None
    if avg_hum is None or not humidity_range:
        return
    if avg_hum < humidity_range[0] - HUMIDITY_VERY_LOW_OFFSET:
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - HUMIDITY_VERY_LOW_INTERVAL_STEP)
        decision.add_reason(
            code=TriggerCode.HUMIDITY_VERY_LOW,
            message=f"very dry air ({avg_hum:.0f}% << ideal {humidity_range[0]:.0f}%)",
            icon="wind",
            interval_delta=-HUMIDITY_VERY_LOW_INTERVAL_STEP,
        )
    elif avg_hum < humidity_range[0] - HUMIDITY_LOW_OFFSET:
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - HUMIDITY_LOW_INTERVAL_STEP)
        decision.add_reason(
            code=TriggerCode.HUMIDITY_LOW,
            message=f"dry air ({avg_hum:.0f}%)",
            icon="wind",
            interval_delta=-HUMIDITY_LOW_INTERVAL_STEP,
        )
    elif avg_hum > humidity_range[1] + HUMIDITY_HIGH_OFFSET:
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + HUMIDITY_HIGH_INTERVAL_STEP)
        decision.add_reason(
            code=TriggerCode.HUMIDITY_HIGH,
            message=f"high ambient humidity ({avg_hum:.0f}%)",
            icon="cloud-rain",
            interval_delta=HUMIDITY_HIGH_INTERVAL_STEP,
        )


def _apply_light_adjustment(decision: IrrigationDecision) -> None:
    avg_light = decision.sensor_snapshot.avg_light if decision.sensor_snapshot else None
    if avg_light is None:
        return
    sf = seasonal_light_factor()
    if avg_light > LIGHT_VERY_BRIGHT * sf:
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - LIGHT_VERY_BRIGHT_INTERVAL_STEP)
        decision.duration_minutes = min(
            MAX_DURATION_MINUTES, decision.duration_minutes + LIGHT_VERY_BRIGHT_DURATION_STEP
        )
        decision.add_reason(
            code=TriggerCode.LIGHT_VERY_BRIGHT,
            message=f"very bright ({avg_light:.0f} lux, seasonal)",
            icon="sun",
            interval_delta=-LIGHT_VERY_BRIGHT_INTERVAL_STEP,
            duration_delta=LIGHT_VERY_BRIGHT_DURATION_STEP,
        )
    elif avg_light > LIGHT_BRIGHT * sf:
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - LIGHT_BRIGHT_INTERVAL_STEP)
        decision.add_reason(
            code=TriggerCode.LIGHT_BRIGHT,
            message=f"bright ({avg_light:.0f} lux, seasonal)",
            icon="sun-dim",
            interval_delta=-LIGHT_BRIGHT_INTERVAL_STEP,
        )
    elif avg_light < LIGHT_VERY_DARK * sf:
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + LIGHT_VERY_DARK_INTERVAL_STEP)
        decision.add_reason(
            code=TriggerCode.LIGHT_VERY_DARK,
            message=f"very low light ({avg_light:.0f} lux, seasonal)",
            icon="moon",
            interval_delta=LIGHT_VERY_DARK_INTERVAL_STEP,
        )
    elif avg_light < LIGHT_DARK * sf:
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + LIGHT_DARK_INTERVAL_STEP)
        decision.add_reason(
            code=TriggerCode.LIGHT_DARK,
            message=f"low light ({avg_light:.0f} lux, seasonal)",
            icon="cloud",
            interval_delta=LIGHT_DARK_INTERVAL_STEP,
        )


def _apply_water_needs_adjustment(decision: IrrigationDecision, water_needs: str) -> None:
    """Nudge dosage by the plants' water demand and record an auditable reason.

    Emits ``WATER_NEEDS_HIGH`` / ``WATER_NEEDS_LOW`` whenever the adjustment
    actually changes the duration or interval (clamping can make it a no-op, in
    which case no reason is added).
    """
    if water_needs not in ("high", "low"):
        return

    prev_duration = decision.duration_minutes
    prev_interval = decision.interval_hours

    if water_needs == "high":
        decision.duration_minutes = max(DEFAULT_DURATION_MINUTES, decision.duration_minutes + WATER_NEEDS_DURATION_STEP)
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - WATER_NEEDS_HIGH_INTERVAL_STEP)
        code = TriggerCode.WATER_NEEDS_HIGH
        message = "high water-needs plants — more water, shorter interval"
        icon = "drop"
    else:
        decision.duration_minutes = max(
            CONFLICT_DURATION_MINUTES, decision.duration_minutes - WATER_NEEDS_DURATION_STEP
        )
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + WATER_NEEDS_LOW_INTERVAL_STEP)
        code = TriggerCode.WATER_NEEDS_LOW
        message = "low water-needs plants — less water, longer interval"
        icon = "drop-half"

    duration_delta = decision.duration_minutes - prev_duration
    interval_delta = decision.interval_hours - prev_interval
    if duration_delta == 0 and interval_delta == 0:
        return

    decision.add_reason(
        code=code,
        message=message,
        icon=icon,
        duration_delta=duration_delta,
        interval_delta=interval_delta,
    )


def _apply_trend_adjustment(decision: IrrigationDecision) -> None:
    trends = decision.trends
    snapshot = decision.sensor_snapshot
    if trends.soil_moisture_trend == "declining":
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - TREND_MOISTURE_INTERVAL_STEP)
        decision.add_reason(
            code=TriggerCode.TREND_MOISTURE_DECLINING,
            message="soil moisture declining",
            icon="trend-down",
            interval_delta=-TREND_MOISTURE_INTERVAL_STEP,
        )
    elif trends.soil_moisture_trend == "rising":
        decision.interval_hours = min(MAX_INTERVAL_HOURS, decision.interval_hours + TREND_MOISTURE_INTERVAL_STEP)
        decision.add_reason(
            code=TriggerCode.TREND_MOISTURE_RISING,
            message="soil moisture rising",
            icon="trend-up",
            interval_delta=TREND_MOISTURE_INTERVAL_STEP,
        )

    avg_temp = snapshot.avg_temperature if snapshot else None
    if trends.temperature_trend == "rising" and avg_temp and avg_temp > TREND_TEMP_RISING_HOT_C:
        decision.interval_hours = max(MIN_INTERVAL_HOURS, decision.interval_hours - TREND_TEMP_RISING_INTERVAL_STEP)
        decision.add_reason(
            code=TriggerCode.TREND_TEMP_RISING,
            message="temperature rising + hot",
            icon="thermometer-hot",
            interval_delta=-TREND_TEMP_RISING_INTERVAL_STEP,
        )

    if trends.irrigation_frequency_low:
        decision.duration_minutes = min(
            MAX_DURATION_MINUTES, decision.duration_minutes + TREND_UNDERWATERING_DURATION_STEP
        )
        decision.add_reason(
            code=TriggerCode.UNDERWATERING_PATTERN,
            message="recent under-watering pattern",
            icon="chart-line-down",
            duration_delta=TREND_UNDERWATERING_DURATION_STEP,
        )


__all__ = ["IrrigationLogic", "Reason"]
