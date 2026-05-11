"""Typed value objects for irrigation decisions and their explanation trail."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Action(StrEnum):
    """Final action recommended by the engine."""

    IRRIGATE = "irrigate"
    SKIP = "skip"


class Severity(StrEnum):
    """Severity of a reason or alert."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class TriggerCode(StrEnum):
    """Stable identifier for every reason the engine can emit.

    Stable codes power the explainability UI, alert dedup, and audit log
    grouping. Adding a new code is non-breaking; renaming one is.
    """

    # Terminal triggers (set the action on their own)
    NO_PLANTS = "no_plants"
    COOLDOWN = "cooldown"
    WATER_WARNING = "water_warning"
    WATER_STRESS = "water_stress"
    OVER_WATERING = "over_watering"
    SENSOR_VERY_DRY = "sensor_very_dry"
    SENSOR_DRY = "sensor_dry"
    SENSOR_ADEQUATE = "sensor_adequate"
    SENSOR_WET = "sensor_wet"
    CONFLICT = "conflict"
    WEATHER_SKIP = "weather_skip"
    TEMP_FALLBACK = "temp_fallback"
    CONFIG_FALLBACK = "config_fallback"
    NO_DATA = "no_data"
    DAILY_CAP_HIT = "daily_cap_hit"
    # Adjustments
    TEMP_HIGH = "temp_high"
    TEMP_LOW = "temp_low"
    HUMIDITY_VERY_LOW = "humidity_very_low"
    HUMIDITY_LOW = "humidity_low"
    HUMIDITY_HIGH = "humidity_high"
    LIGHT_VERY_BRIGHT = "light_very_bright"
    LIGHT_BRIGHT = "light_bright"
    LIGHT_DARK = "light_dark"
    LIGHT_VERY_DARK = "light_very_dark"
    WATER_NEEDS_HIGH = "water_needs_high"
    WATER_NEEDS_LOW = "water_needs_low"
    TREND_MOISTURE_DECLINING = "trend_moisture_declining"
    TREND_MOISTURE_RISING = "trend_moisture_rising"
    TREND_TEMP_RISING = "trend_temp_rising"
    UNDERWATERING_PATTERN = "underwatering_pattern"
    LEARNING_ALERT = "learning_alert"


class Reason(BaseModel):
    """A single line in the decision's explanation trail.

    Reasons render as cards in the UI; their `code` is stable so the
    UI/MCP can map to icons and copy without parsing free-text messages.
    """

    model_config = ConfigDict(frozen=True)

    code: TriggerCode
    message: str
    severity: Severity = Severity.INFO
    icon: str | None = None
    duration_delta: int = 0
    interval_delta: int = 0


class PerSensorSnapshot(BaseModel):
    """Per-sensor aggregate over the lookback window."""

    model_config = ConfigDict(frozen=True)

    sensor_id: int
    plant_id: int | None = None
    name: str
    avg_temperature: float | None = None
    avg_humidity: float | None = None
    avg_soil_moisture: float | None = None


class SensorSnapshot(BaseModel):
    """Cluster-wide sensor aggregate captured at decision time."""

    model_config = ConfigDict(frozen=True)

    avg_temperature: float | None = None
    avg_env_humidity: float | None = None
    avg_soil_moisture: float | None = None
    min_soil_moisture: float | None = None
    max_soil_moisture: float | None = None
    avg_light: float | None = None
    per_sensor: list[PerSensorSnapshot] = Field(default_factory=list)
    water_warnings: list[str] = Field(default_factory=list)

    @property
    def has_data(self) -> bool:
        """True when at least one cluster-wide aggregate is populated."""
        return any(
            getattr(self, attr) is not None
            for attr in ("avg_temperature", "avg_env_humidity", "avg_soil_moisture", "avg_light")
        )


class StressIndicators(BaseModel):
    """Detected stress conditions; populated even when not decisive."""

    model_config = ConfigDict(frozen=False)

    water_warning: str | None = None
    water_stress: str | None = None
    heat_stress: str | None = None
    over_watering: str | None = None
    low_env_humidity: str | None = None
    low_light: str | None = None
    learning_alerts: list[dict] = Field(default_factory=list)

    def any_critical(self) -> bool:
        """True when at least one critical-class stress is set."""
        return any(
            value is not None for value in (self.water_warning, self.water_stress, self.over_watering, self.heat_stress)
        )


class Trends(BaseModel):
    """Historical trend signals for the cluster's recent behaviour."""

    model_config = ConfigDict(frozen=False)

    soil_moisture_trend: str | None = None
    soil_moisture_delta: float = 0.0
    temperature_trend: str | None = None
    irrigation_frequency_low: bool = False
    irrigation_frequency_high: bool = False


class WeatherSnapshot(BaseModel):
    """Compact weather context used by the engine and audit log."""

    model_config = ConfigDict(frozen=True)

    temperature: float | None = None
    feels_like: float | None = None
    humidity: float | None = None
    precipitation_next_6h_mm: float | None = None
    source: str | None = None


class IrrigationDecision(BaseModel):
    """The full, typed output of the irrigation decision engine.

    Carries action + dosage + a structured reason trail so the UI, MCP,
    and audit log can explain *why*. Persisted to ``decision_logs`` for
    every evaluation (acted-on or not).
    """

    model_config = ConfigDict(from_attributes=True)

    cluster_id: int
    evaluated_at: int
    action: Action
    duration_minutes: int
    interval_hours: int
    confidence: float
    reasons: list[Reason] = Field(default_factory=list)
    sensor_snapshot: SensorSnapshot | None = None
    stress_indicators: StressIndicators = Field(default_factory=StressIndicators)
    trends: Trends = Field(default_factory=Trends)
    weather: WeatherSnapshot | None = None

    @property
    def reason_text(self) -> str:
        """Composite ``; ``-joined human reason text."""
        return "; ".join(r.message for r in self.reasons) or "no specific conditions"

    @property
    def primary_code(self) -> TriggerCode | None:
        """The first (most decisive) reason code, if any."""
        return self.reasons[0].code if self.reasons else None

    def add_reason(
        self,
        code: TriggerCode,
        message: str,
        *,
        severity: Severity = Severity.INFO,
        icon: str | None = None,
        duration_delta: int = 0,
        interval_delta: int = 0,
    ) -> None:
        """Append a Reason; mutates the model in place."""
        self.reasons.append(
            Reason(
                code=code,
                message=message,
                severity=severity,
                icon=icon,
                duration_delta=duration_delta,
                interval_delta=interval_delta,
            )
        )
