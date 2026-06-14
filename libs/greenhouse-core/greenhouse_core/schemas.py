"""Pydantic v2 request/response schemas for the irrigation API."""

import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Cluster ---


class ClusterBase(BaseModel):
    name: str
    location: str | None = None
    environment: str = "indoor"


class CreateClusterRequest(ClusterBase):
    pass


class ClusterResponse(ClusterBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: int


# Forward references to schemas defined further down — populated via
# model_rebuild() at the end of this module.
class ClusterDetailResponse(BaseModel):
    """Cluster plus its child resources (plants, sensors, irrigators, config, windows).

    Returned by GET /clusters/{id}?expand=children. Plain GET without the
    expand flag continues to return ClusterResponse for back-compat.
    """

    cluster: "ClusterResponse"
    plants: list["PlantResponse"]
    sensors: list["SensorResponse"]
    irrigator: "IrrigatorResponse | None"
    config: "ConfigResponse | None"
    windows: list["IrrigationWindowResponse"]


# --- Plant ---


class PlantBase(BaseModel):
    species: str
    category: str | None = None
    water_needs: str | None = None
    light_needs: str | None = None
    ideal_temp_min: float | None = None
    ideal_temp_max: float | None = None
    ideal_humidity_min: float | None = None
    ideal_humidity_max: float | None = None
    notes: str | None = None


class CreatePlantRequest(PlantBase):
    pass


class PlantResponse(PlantBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int


class PlantListResponse(BaseModel):
    """Paginated top-level plant listing across all clusters."""

    plants: list[PlantResponse]
    next_cursor: int | None = None


class SyncPlantsRequest(BaseModel):
    plant_id: int | None = None
    cluster_id: int | None = None


class MovePlantRequest(BaseModel):
    """Body for POST /plants/{plant_id}/move."""

    target_cluster_id: int


class SyncPlantsResponse(BaseModel):
    synced: int
    errors: list[str]


# --- Irrigator ---


class IrrigatorBase(BaseModel):
    tuya_device_id: str
    name: str
    type: str
    config: dict | None = None
    reservoir_l: float | None = Field(default=None, ge=0)
    flow_rate_l_per_min: float | None = Field(default=None, ge=0)


class CreateIrrigatorRequest(IrrigatorBase):
    pass


class IrrigatorResponse(IrrigatorBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int

    @field_validator("config", mode="before")
    @classmethod
    def parse_config(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class IrrigatorListResponse(BaseModel):
    """Paginated top-level irrigator listing across all clusters."""

    irrigators: list[IrrigatorResponse]
    next_cursor: int | None = None


class StartIrrigatorRequest(BaseModel):
    minutes: int | None = None


class LogManualRequest(BaseModel):
    minutes: int
    notes: str | None = None


class IrrigatorActionResponse(BaseModel):
    """Result of a manual irrigator command (start/stop)."""

    success: bool
    message: str


class LogManualResponse(BaseModel):
    """Result of logging a manual irrigation event."""

    success: bool
    event_id: int


class SuccessResponse(BaseModel):
    """Generic success acknowledgement."""

    success: bool


# --- Sensor ---


class SensorBase(BaseModel):
    tuya_device_id: str
    name: str
    type: str
    config: dict | None = None
    plant_id: int | None = None


class CreateSensorRequest(SensorBase):
    pass


class SensorResponse(SensorBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int

    @field_validator("config", mode="before")
    @classmethod
    def parse_config(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class SensorListResponse(BaseModel):
    """Paginated top-level sensor listing across all clusters."""

    sensors: list[SensorResponse]
    next_cursor: int | None = None


# --- Sensor Assignment History ---


class SensorAssignmentResponse(BaseModel):
    """One row from a sensor's plant-assignment history. ``ended_at=None``
    means the row is currently active."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sensor_id: int
    plant_id: int
    started_at: int
    ended_at: int | None


class SensorAssignmentListResponse(BaseModel):
    """Full assignment history for a sensor, oldest first."""

    sensor_id: int
    assignments: list[SensorAssignmentResponse]


# --- Irrigation Window ---


class IrrigationWindowBase(BaseModel):
    """Common shape for create/update of a per-cluster watering window.

    Hours are local-time integers 0–23, end-exclusive. ``weekday_mask`` is a
    Mon-bit-1 bitmask (Mon=1, Tue=2, …, Sun=64); 127 = every day.
    """

    start_hour: int
    end_hour: int
    weekday_mask: int = 127
    label: str | None = None


class CreateIrrigationWindowRequest(IrrigationWindowBase):
    pass


class UpdateIrrigationWindowRequest(BaseModel):
    start_hour: int | None = None
    end_hour: int | None = None
    weekday_mask: int | None = None
    label: str | None = None


class IrrigationWindowResponse(IrrigationWindowBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int


class IrrigationWindowListResponse(BaseModel):
    cluster_id: int
    windows: list[IrrigationWindowResponse]


# --- Sensor Reading ---


class SensorReadingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sensor_id: int
    timestamp: int
    temperature: float | None
    soil_moisture: float | None
    light: int | None
    env_humidity: float | None
    battery_state: str | None
    water_warning: bool | None


# --- Irrigation Event ---


class IrrigationEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    irrigator_id: int
    timestamp: int
    action: str
    duration_minutes: int | None
    triggered_by: str
    notes: str | None


# --- Irrigation Config ---


class SetConfigRequest(BaseModel):
    """Partial update for a cluster's irrigation config.

    Every field is optional and nullable: null means "inherit from the
    global default." Omitting a field leaves the current value unchanged
    so the form can PATCH a single override without round-tripping the
    whole config.

    Quiet hours: ``quiet_start_hour == quiet_end_hour`` means quiet hours
    are explicitly disabled at the cluster level (opt out of an inherited
    global window). Both null = inherit.
    """

    mode: str | None = None
    duration_minutes: int | None = None
    interval_hours: int | None = None
    auto_run: bool | None = None
    daily_cap_minutes: int | None = None
    max_events_per_day: int | None = None
    quiet_start_hour: int | None = None
    quiet_end_hour: int | None = None


class ConfigResponse(BaseModel):
    """Declared (raw) cluster config — nulls represent inherited values."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int
    mode: str | None
    duration_minutes: int | None
    interval_hours: int | None
    auto_run: bool | None
    last_updated: int
    daily_cap_minutes: int | None = None
    max_events_per_day: int | None = None
    quiet_start_hour: int | None = None
    quiet_end_hour: int | None = None


class GlobalConfigResponse(BaseModel):
    """Singleton global irrigation defaults.

    Same field set as :class:`ConfigResponse` minus the cluster pointer.
    Every field nullable; a null here means "fall through to the
    project-wide constant in :mod:`constants`."
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    mode: str | None
    duration_minutes: int | None
    interval_hours: int | None
    auto_run: bool | None
    daily_cap_minutes: int | None
    max_events_per_day: int | None
    quiet_start_hour: int | None
    quiet_end_hour: int | None
    last_updated: int


class UpdateGlobalConfigRequest(BaseModel):
    """Partial update for the singleton global irrigation config.

    Omitted fields leave the stored value unchanged; pass null explicitly
    only when you want to clear a previously set default (falling back to
    the project-wide constant).
    """

    mode: str | None = None
    duration_minutes: int | None = None
    interval_hours: int | None = None
    auto_run: bool | None = None
    daily_cap_minutes: int | None = None
    max_events_per_day: int | None = None
    quiet_start_hour: int | None = None
    quiet_end_hour: int | None = None


class ResolvedConfigField(BaseModel):
    """A single field's resolved value plus its provenance.

    ``source`` is one of ``"cluster"`` (overridden at the cluster level),
    ``"global"`` (inherited from the global defaults row) or ``"default"``
    (no value set at either level, falling back to a project-wide constant).
    """

    value: int | float | str | bool | None
    source: str


class EffectiveConfigResponse(BaseModel):
    """Cluster config merged with global defaults and built-in constants.

    ``declared`` is the raw per-cluster row (with nulls preserved so the UI
    can show inheritance state). ``effective`` is the merged view: every
    field carries the resolved value and the source level so the UI can
    render the "↳ default" / "override" badge without re-querying.
    """

    cluster_id: int
    declared: ConfigResponse | None
    effective: dict[str, ResolvedConfigField]


# --- Operations ---


class IrrigateRequest(BaseModel):
    """Body for POST /clusters/{id}/irrigate.

    ``force=True`` is the explicit override switch used when the caller
    wants to bypass the quiet-hours deny window. The decision engine still
    runs every other rule (cooldown, stress, weather, sensors) and the
    final decision is tagged with a ``manual_override_quiet_hours`` warning
    Reason so the audit log records the override.
    """

    temp_override: float | None = None
    dry_run: bool = False
    no_sync: bool = False
    force: bool = False


class AlertResponse(BaseModel):
    type: str
    severity: str
    message: str
    data: dict | None = None


class ReasonResponse(BaseModel):
    """A single line in the decision's structured explanation trail."""

    code: str
    message: str
    severity: str = "info"
    icon: str | None = None
    duration_delta: int = 0
    interval_delta: int = 0


class IrrigateResponse(BaseModel):
    action: str
    reason: str
    confidence: float
    duration_minutes: int | None = None
    interval_hours: int | None = None
    stress_indicators: dict | None = None
    reasons: list[ReasonResponse] = []
    learning_alerts: list[AlertResponse] = []
    temperature: float | None = None
    temperature_source: str | None = None


class SensorStatusResponse(BaseModel):
    sensor_id: int
    sensor_name: str
    plant_species: str | None
    soil_moisture: float | None
    status: str
    target_min: float | None
    target_max: float | None


class MonitorResponse(BaseModel):
    cluster_name: str
    sensors: list[SensorStatusResponse]
    needs_water: list[str]


class CheckClusterResponse(BaseModel):
    cluster_id: int
    cluster_name: str
    action: str
    notes: str | None = None
    alerts: list[AlertResponse] = []
    maintenance: list[AlertResponse] = []
    needs_water: list[str] = []


class CheckAllResponse(BaseModel):
    results: list[CheckClusterResponse]
    has_alerts: bool


class SyncRequest(BaseModel):
    hours: int = 24


class SyncResponse(BaseModel):
    total_synced: int
    total_new: int
    total_live: int
    errors: list[str]


class LearnResponse(BaseModel):
    cluster_name: str
    report: str
    alerts: list[AlertResponse] = []


class SensorHistoryResponse(BaseModel):
    sensor_id: int
    sensor_name: str
    readings: list[SensorReadingResponse]


class IrrigatorHistoryResponse(BaseModel):
    irrigator_id: int
    irrigator_name: str
    events: list[IrrigationEventResponse]


class HistoryResponse(BaseModel):
    cluster_name: str
    sensors: list[SensorHistoryResponse]
    irrigators: list[IrrigatorHistoryResponse]


class StatsResponse(BaseModel):
    cluster_name: str
    period_days: int
    total_events: int
    total_duration_minutes: int
    avg_duration_minutes: float
    frequency_per_day: float
    events_by_type: dict[str, int]
    events_by_trigger: dict[str, int]


class ClusterStatusSensorResponse(BaseModel):
    id: int
    name: str
    type: str
    plant_id: int | None
    last_reading: SensorReadingResponse | None
    reading_age_seconds: int | None


class ClusterStatusIrrigatorResponse(BaseModel):
    id: int
    name: str
    type: str
    recent_event_count: int
    last_event: IrrigationEventResponse | None


class ClusterStatusResponse(BaseModel):
    cluster: ClusterResponse
    config: ConfigResponse | None
    plants: list[PlantResponse]
    sensors: list[ClusterStatusSensorResponse]
    irrigator: ClusterStatusIrrigatorResponse | None
    decision: IrrigateResponse | None


# --- Scheduler ---


class SchedulerJobResponse(BaseModel):
    id: str
    name: str
    trigger: str
    next_run_time: str | None
    paused: bool = False


class CreateSchedulerJobRequest(BaseModel):
    name: str
    job_type: str
    cron_expression: str | None = None
    interval_minutes: int | None = None


class SchedulerStateResponse(BaseModel):
    """Runtime state of the check_all scheduler job."""

    paused: bool


# --- Health ---


class HealthResponse(BaseModel):
    status: str
    scheduler_running: bool
    jobs: list[SchedulerJobResponse]


# --- Chart data (for Chart.js) ---


class ChartDatasetResponse(BaseModel):
    sensor_id: int
    sensor_name: str
    points: list[tuple[int, float]]


class ChartEventResponse(BaseModel):
    timestamp: int
    action: str
    duration_minutes: int | None = None


class ChartThresholdResponse(BaseModel):
    min: float | None = None
    max: float | None = None
    source: str = "none"


class ChartPayloadResponse(BaseModel):
    metric: str
    hours: int
    datasets: list[ChartDatasetResponse]
    events: list[ChartEventResponse]
    threshold: ChartThresholdResponse


# --- Alerts ------------------------------------------------------------------


class AlertSummary(BaseModel):
    """Persisted alert row exposed via the inbox API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    code: str
    severity: str
    entity_type: str
    entity_id: int | None
    cluster_id: int | None
    plant_id: int | None
    title: str
    message: str
    status: str
    first_seen_at: int
    last_seen_at: int
    occurrence_count: int
    acknowledged_at: int | None = None
    resolved_at: int | None = None


class AlertListResponse(BaseModel):
    open_count: int
    items: list[AlertSummary]
    next_cursor: int | None = None


# --- Activity log ------------------------------------------------------------


class ActivityEventResponse(BaseModel):
    """One row in the cross-cutting activity stream."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: int
    source: str
    entity_type: str
    entity_id: int | None
    severity: str
    code: str
    message: str


class ActivityListResponse(BaseModel):
    items: list[ActivityEventResponse]
    next_cursor: int | None = None


# --- Decisions ---------------------------------------------------------------


class DecisionLogResponse(BaseModel):
    """A single persisted decision evaluation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int
    evaluated_at: int
    action: str
    duration_minutes: int
    interval_hours: int
    confidence: float
    primary_code: str | None
    reason_text: str
    triggered_by: str
    actuated: bool


class DecisionLogListResponse(BaseModel):
    cluster_id: int
    items: list[DecisionLogResponse]


# --- Forecast ---------------------------------------------------------------


class ForecastResponse(BaseModel):
    """Predicted-next-irrigation forecast for a cluster."""

    cluster_id: int
    next_predicted_at: int | None
    hours_until_next: float | None
    projected_min_moisture: float | None
    method: str
    confidence: float
    explanation: str
    weather_skip: bool = False
    weather_reason: str | None = None
    precipitation_next_6h_mm: float | None = None


# --- Plant health ----------------------------------------------------------


class PlantHealthDailyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date_key: str
    timestamp: int
    score: float
    soil_in_band_pct: float | None = None
    temp_in_band_pct: float | None = None
    humidity_in_band_pct: float | None = None
    efficiency: float | None = None
    sample_count: int


class PlantHealthResponse(BaseModel):
    plant_id: int
    species: str
    current_score: float | None
    history: list[PlantHealthDailyResponse]


# --- System health pulse ---------------------------------------------------


class SystemHealthDevice(BaseModel):
    id: int
    name: str
    status: str
    age_seconds: int | None = None
    note: str | None = None


class SystemHealthResponse(BaseModel):
    status: str
    scheduler_running: bool
    cloud_reachable: bool
    last_sync_at: int | None
    sensors_total: int
    sensors_stale: int
    sensors_fresh: int
    irrigators_total: int
    open_alerts: int
    devices: list[SystemHealthDevice]


# --- Data quality ----------------------------------------------------------


class DataQualityIssue(BaseModel):
    code: str
    severity: str
    entity_type: str
    entity_id: int | None
    label: str
    message: str


class DataQualityReport(BaseModel):
    issues: list[DataQualityIssue]
    counts: dict[str, int]


# --- Vacation --------------------------------------------------------------


class VacationCreateRequest(BaseModel):
    starts_at: int
    ends_at: int
    contact_email: str | None = None
    notes: str | None = None


class UpdateVacationWindowRequest(BaseModel):
    """Partial-update body for a vacation window. All fields optional."""

    starts_at: int | None = None
    ends_at: int | None = None
    contact_email: str | None = None
    notes: str | None = None


class VacationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    starts_at: int
    ends_at: int
    contact_email: str | None
    notes: str | None
    created_at: int


class VacationListResponse(BaseModel):
    active: VacationResponse | None
    items: list[VacationResponse]


# --- Preferences -----------------------------------------------------------


class PreferencesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    units: str
    timezone: str
    theme: str
    default_cluster_id: int | None
    refresh_interval_seconds: int
    dry_run_global: bool
    scheduler_paused: bool
    notify_manual: bool
    notify_emergency: bool
    notify_alerts: bool
    notify_auto: bool


class PreferencesUpdateRequest(BaseModel):
    units: str | None = None
    timezone: str | None = None
    theme: str | None = None
    default_cluster_id: int | None = None
    refresh_interval_seconds: int | None = None
    dry_run_global: bool | None = None
    notify_manual: bool | None = None
    notify_emergency: bool | None = None
    notify_alerts: bool | None = None
    notify_auto: bool | None = None


# --- Edit/Delete bodies ---------------------------------------------------


class UpdateClusterRequest(BaseModel):
    name: str | None = None
    location: str | None = None
    environment: str | None = None


class UpdatePlantRequest(BaseModel):
    species: str | None = None
    category: str | None = None
    water_needs: str | None = None
    light_needs: str | None = None
    ideal_temp_min: float | None = None
    ideal_temp_max: float | None = None
    ideal_humidity_min: float | None = None
    ideal_humidity_max: float | None = None
    notes: str | None = None


class UpdateSensorRequest(BaseModel):
    name: str | None = None
    type: str | None = None
    config: dict | None = None
    plant_id: int | None = None


class UpdateIrrigatorRequest(BaseModel):
    name: str | None = None
    type: str | None = None
    config: dict | None = None
    reservoir_l: float | None = Field(default=None, ge=0)
    flow_rate_l_per_min: float | None = Field(default=None, ge=0)


# --- Search ----------------------------------------------------------------


class SearchHit(BaseModel):
    entity_type: str
    entity_id: int
    label: str
    sublabel: str | None = None
    href: str


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


# --- Insights ---------------------------------------------------------------


class CareInsight(BaseModel):
    """A single actionable insight surfaced in the UI."""

    code: str
    severity: str
    title: str
    message: str
    suggestion: str | None = None


class ClusterInsightsResponse(BaseModel):
    cluster_id: int
    cluster_name: str
    insights: list[CareInsight]
    forecast: ForecastResponse | None = None


# --- Bulk operations --------------------------------------------------------


class StopAllResponse(BaseModel):
    """Result of a bulk emergency stop of all irrigators."""

    stopped: int
    errors: list[str]


# --- Irrigation efficacy ----------------------------------------------------


class EfficacyItemResponse(BaseModel):
    """Scored outcome for a single completed irrigation event."""

    event_id: int
    timestamp: int
    irrigator_name: str
    duration_minutes: int
    before_pct: float | None
    after_pct: float | None
    score: float | None


class EfficacyListResponse(BaseModel):
    cluster_id: int
    days: int
    items: list[EfficacyItemResponse]


# --- Premium viz schemas ---


class OverlayDataset(BaseModel):
    """One normalised series in the multi-metric overlay chart."""

    metric: str
    points: list[tuple[int, float]]
    original_max: float | None = None


class MultiMetricOverlayResponse(BaseModel):
    """Payload for the multi-metric overlay chart (soil, humidity, light)."""

    cluster_id: int
    hours: int
    datasets: list[OverlayDataset]
    events: list[ChartEventResponse]
    normalised: bool = True


class HeatmapCell(BaseModel):
    """A single cell in the irrigation heatmap grid."""

    weekday: int
    hour: int
    count: int
    total_minutes: int


class HeatmapResponse(BaseModel):
    """Payload for the 7x24 irrigation heatmap."""

    cluster_id: int
    days: int
    cells: list[HeatmapCell]


class PlantHealthTimelineResponse(BaseModel):
    """90-day daily health score timeline for a single plant."""

    plant_id: int
    points: list[tuple[int, float]]
    thresholds: dict[str, float] = {"good": 80.0, "ok": 50.0}


# Resolve forward references on schemas that compose types defined later in
# the module (ClusterDetailResponse points at PlantResponse, SensorResponse,
# IrrigatorResponse, ConfigResponse, IrrigationWindowResponse, all of which
# live further down).
ClusterDetailResponse.model_rebuild()
