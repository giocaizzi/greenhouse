"""SQLAlchemy v2 ORM models for the irrigation system."""

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


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

    cluster: Mapped["Cluster"] = relationship(back_populates="config")
