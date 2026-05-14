"""SQLAlchemy v2 ORM models for the irrigation system."""

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Activity / audit / decisions / alerts ────────────────────────────────────
# Cross-cutting tables. They reference cluster_id/plant_id/sensor_id/etc. by
# integer id rather than hard FKs so the audit trail survives cascade deletes
# and the notification inbox can deduplicate across resource types.
ENTITY_CLUSTER = "cluster"
ENTITY_PLANT = "plant"
ENTITY_SENSOR = "sensor"
ENTITY_IRRIGATOR = "irrigator"
ENTITY_SYSTEM = "system"


class Cluster(Base):
    """A cluster of plants irrigated by the same device."""

    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    environment: Mapped[str] = mapped_column(String, default="indoor")

    plants: Mapped[list["Plant"]] = relationship(back_populates="cluster", cascade="all, delete-orphan")
    irrigators: Mapped[list["Irrigator"]] = relationship(back_populates="cluster", cascade="all, delete-orphan")
    sensors: Mapped[list["Sensor"]] = relationship(back_populates="cluster", cascade="all, delete-orphan")
    config: Mapped["IrrigationConfig | None"] = relationship(
        back_populates="cluster", cascade="all, delete-orphan", uselist=False
    )


class Plant(Base):
    """A plant in a cluster."""

    __tablename__ = "plants"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), nullable=False)
    species: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str | None] = mapped_column(String)
    water_needs: Mapped[str | None] = mapped_column(String)
    light_needs: Mapped[str | None] = mapped_column(String)
    ideal_temp_min: Mapped[float | None] = mapped_column()
    ideal_temp_max: Mapped[float | None] = mapped_column()
    ideal_humidity_min: Mapped[float | None] = mapped_column()
    ideal_humidity_max: Mapped[float | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(String)

    cluster: Mapped["Cluster"] = relationship(back_populates="plants")
    sensors: Mapped[list["Sensor"]] = relationship(back_populates="plant")


class Irrigator(Base):
    """An irrigation device (Tuya-based)."""

    __tablename__ = "irrigators"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), nullable=False)
    tuya_device_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    config: Mapped[str | None] = mapped_column(String)

    cluster: Mapped["Cluster"] = relationship(back_populates="irrigators")
    events: Mapped[list["IrrigationEvent"]] = relationship(back_populates="irrigator", cascade="all, delete-orphan")


class Sensor(Base):
    """A sensor device (Tuya-based)."""

    __tablename__ = "sensors"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), nullable=False)
    tuya_device_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    config: Mapped[str | None] = mapped_column(String)
    plant_id: Mapped[int | None] = mapped_column(ForeignKey("plants.id"))

    cluster: Mapped["Cluster"] = relationship(back_populates="sensors")
    plant: Mapped["Plant | None"] = relationship(back_populates="sensors")
    readings: Mapped[list["SensorReading"]] = relationship(back_populates="sensor", cascade="all, delete-orphan")


class SensorReading(Base):
    """A sensor reading (time-series, deduplicated)."""

    __tablename__ = "sensor_readings"
    __table_args__ = (
        UniqueConstraint("sensor_id", "timestamp"),
        Index("idx_sensor_readings_timestamp", "timestamp"),
        Index("idx_sensor_readings_sensor_id", "sensor_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.id"), nullable=False)
    timestamp: Mapped[int] = mapped_column(Integer, nullable=False)
    temperature: Mapped[float | None] = mapped_column()
    soil_moisture: Mapped[float | None] = mapped_column()
    light: Mapped[int | None] = mapped_column(Integer)
    env_humidity: Mapped[float | None] = mapped_column()
    battery_state: Mapped[str | None] = mapped_column(String)
    water_warning: Mapped[bool | None] = mapped_column()

    sensor: Mapped["Sensor"] = relationship(back_populates="readings")


class IrrigationEvent(Base):
    """An irrigation event (start, stop, etc.)."""

    __tablename__ = "irrigation_events"
    __table_args__ = (
        Index("idx_irrigation_events_timestamp", "timestamp"),
        Index("idx_irrigation_events_irrigator_id", "irrigator_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    irrigator_id: Mapped[int] = mapped_column(ForeignKey("irrigators.id"), nullable=False)
    timestamp: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    triggered_by: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str | None] = mapped_column(String)

    irrigator: Mapped["Irrigator"] = relationship(back_populates="events")


class IrrigationConfig(Base):
    """Irrigation configuration for a cluster."""

    __tablename__ = "irrigation_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), nullable=False, unique=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    interval_hours: Mapped[int | None] = mapped_column(Integer)
    auto_run: Mapped[bool] = mapped_column(nullable=False)
    last_updated: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_cap_minutes: Mapped[int | None] = mapped_column(Integer)
    max_events_per_day: Mapped[int | None] = mapped_column(Integer)

    cluster: Mapped["Cluster"] = relationship(back_populates="config")


class DecisionLog(Base):
    """Persisted record of every irrigation engine evaluation.

    Stored whether or not the decision was acted on, so the audit log can
    answer "why did you skip at 3am?" and the explainability UI can replay
    the reasoning trail.
    """

    __tablename__ = "decision_logs"
    __table_args__ = (
        Index("idx_decision_logs_cluster_id", "cluster_id"),
        Index("idx_decision_logs_evaluated_at", "evaluated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    interval_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    primary_code: Mapped[str | None] = mapped_column(String)
    reason_text: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[str] = mapped_column(String, nullable=False)
    triggered_by: Mapped[str] = mapped_column(String, nullable=False, default="auto")
    actuated: Mapped[bool] = mapped_column(nullable=False, default=False)


class ActivityEvent(Base):
    """Polymorphic cross-cutting activity stream.

    Powers /activity timelines, daily digests, and notification dedup.
    `entity_type` is one of the ``ENTITY_*`` constants.
    """

    __tablename__ = "activity_events"
    __table_args__ = (
        Index("idx_activity_events_timestamp", "timestamp"),
        Index("idx_activity_events_entity", "entity_type", "entity_id"),
        Index("idx_activity_events_source", "source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="info")
    code: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[str | None] = mapped_column(String)


class Alert(Base):
    """Persisted, deduplicated alert with ack/resolve lifecycle.

    A single ``dedup_key`` (stable per source+entity+code+plant) collapses
    repeat detections so the inbox stays signal-rich. ``status`` flows
    ``open → acknowledged → resolved``.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        Index("idx_alerts_status", "status"),
        Index("idx_alerts_dedup_key", "dedup_key"),
        Index("idx_alerts_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="info")
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    cluster_id: Mapped[int | None] = mapped_column(Integer)
    plant_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    first_seen_at: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seen_at: Mapped[int] = mapped_column(Integer, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    acknowledged_at: Mapped[int | None] = mapped_column(Integer)
    resolved_at: Mapped[int | None] = mapped_column(Integer)


class PlantHealthDaily(Base):
    """Daily snapshot of per-plant composite health score.

    Score is a 0–100 composite of in-band soil/temp/humidity time +
    learning-derived efficiency. Stored daily so the UI can plot
    long-horizon trends without recomputing from raw readings.
    """

    __tablename__ = "plant_health_daily"
    __table_args__ = (
        UniqueConstraint("plant_id", "date_key"),
        Index("idx_plant_health_daily_plant", "plant_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    date_key: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(nullable=False)
    soil_in_band_pct: Mapped[float | None] = mapped_column()
    temp_in_band_pct: Mapped[float | None] = mapped_column()
    humidity_in_band_pct: Mapped[float | None] = mapped_column()
    efficiency: Mapped[float | None] = mapped_column()
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class VacationWindow(Base):
    """Active vacation window — the engine and digest channels honor it."""

    __tablename__ = "vacation_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    starts_at: Mapped[int] = mapped_column(Integer, nullable=False)
    ends_at: Mapped[int] = mapped_column(Integer, nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class UserPreferences(Base):
    """Single-row preferences table (single-user app).

    Filters in web/filters.py consult these so timestamps, units, and
    theme respect user choices server-side instead of via localStorage.
    """

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    units: Mapped[str] = mapped_column(String, nullable=False, default="metric")
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    theme: Mapped[str] = mapped_column(String, nullable=False, default="auto")
    default_cluster_id: Mapped[int | None] = mapped_column(Integer)
    refresh_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    dry_run_global: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Runtime kill switch for the APScheduler `check_all` job. Mutated by
    # the /scheduler/pause and /scheduler/resume endpoints and re-applied on
    # server startup so the pause survives a container restart.
    scheduler_paused: Mapped[bool] = mapped_column(nullable=False, default=False)
