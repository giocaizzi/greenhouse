"""Pydantic v2 request/response schemas for the irrigation API."""

import json

from pydantic import BaseModel, ConfigDict, field_validator

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


class SyncPlantsRequest(BaseModel):
    plant_id: int | None = None
    cluster_id: int | None = None


class SyncPlantsResponse(BaseModel):
    synced: int
    errors: list[str]


# --- Irrigator ---


class IrrigatorBase(BaseModel):
    tuya_device_id: str
    name: str
    type: str
    config: dict | None = None


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


class StartIrrigatorRequest(BaseModel):
    minutes: int | None = None


class LogManualRequest(BaseModel):
    minutes: int
    notes: str | None = None


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
    mode: str
    duration_minutes: int | None = None
    interval_hours: int | None = None
    auto_run: bool = True


class ConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int
    mode: str
    duration_minutes: int | None
    interval_hours: int | None
    auto_run: bool
    last_updated: int


# --- Operations ---


class IrrigateRequest(BaseModel):
    temp_override: float | None = None
    dry_run: bool = False
    no_sync: bool = False


class AlertResponse(BaseModel):
    type: str
    severity: str
    message: str
    data: dict | None = None


class IrrigateResponse(BaseModel):
    action: str
    reason: str
    confidence: float
    duration_minutes: int | None = None
    interval_hours: int | None = None
    stress_indicators: dict | None = None
    learning_alerts: list[AlertResponse] = []


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
    irrigators: list[ClusterStatusIrrigatorResponse]
    decision: IrrigateResponse | None


# --- Scheduler ---


class SchedulerJobResponse(BaseModel):
    id: str
    name: str
    trigger: str
    next_run_time: str | None


class CreateSchedulerJobRequest(BaseModel):
    name: str
    job_type: str
    cron_expression: str | None = None
    interval_minutes: int | None = None


# --- Health ---


class HealthResponse(BaseModel):
    status: str
    scheduler_running: bool
    jobs: list[SchedulerJobResponse]
