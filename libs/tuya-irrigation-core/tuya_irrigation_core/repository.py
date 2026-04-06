"""Database operations for the irrigation system (replaces IrrigationDB)."""

import json
import time

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from tuya_irrigation_core.models import (
    Cluster,
    IrrigationConfig,
    IrrigationEvent,
    Irrigator,
    Plant,
    Sensor,
    SensorReading,
)


class IrrigationRepository:
    """Repository for irrigation system data access."""

    def __init__(self, session: Session):
        self.session = session

    # ── Clusters ──────────────────────────────────────────────────────────────

    def add_cluster(self, name: str, location: str | None = None, environment: str = "indoor") -> int:
        """Add a new cluster and return its ID."""
        cluster = Cluster(name=name, location=location, created_at=int(time.time()), environment=environment)
        self.session.add(cluster)
        self.session.flush()
        return cluster.id

    def get_cluster(self, cluster_id: int) -> Cluster | None:
        """Get a cluster by ID."""
        return self.session.get(Cluster, cluster_id)

    def list_clusters(self) -> list[Cluster]:
        """List all clusters ordered by name."""
        return list(self.session.scalars(select(Cluster).order_by(Cluster.name)))

    # ── Plants ────────────────────────────────────────────────────────────────

    def add_plant(
        self,
        cluster_id: int,
        species: str,
        category: str | None = None,
        water_needs: str | None = None,
        light_needs: str | None = None,
        ideal_temp_min: float | None = None,
        ideal_temp_max: float | None = None,
        ideal_humidity_min: float | None = None,
        ideal_humidity_max: float | None = None,
        notes: str | None = None,
    ) -> int:
        """Add a plant to a cluster and return its ID."""
        plant = Plant(
            cluster_id=cluster_id,
            species=species,
            category=category,
            water_needs=water_needs,
            light_needs=light_needs,
            ideal_temp_min=ideal_temp_min,
            ideal_temp_max=ideal_temp_max,
            ideal_humidity_min=ideal_humidity_min,
            ideal_humidity_max=ideal_humidity_max,
            notes=notes,
        )
        self.session.add(plant)
        self.session.flush()
        return plant.id

    def get_plants_in_cluster(self, cluster_id: int) -> list[Plant]:
        """Get all plants in a cluster ordered by species."""
        return list(self.session.scalars(select(Plant).where(Plant.cluster_id == cluster_id).order_by(Plant.species)))

    # ── Irrigators ────────────────────────────────────────────────────────────

    def add_irrigator(
        self,
        cluster_id: int,
        tuya_device_id: str,
        name: str,
        irrigator_type: str,
        config: dict,
    ) -> int:
        """Add an irrigator device and return its ID."""
        irrigator = Irrigator(
            cluster_id=cluster_id,
            tuya_device_id=tuya_device_id,
            name=name,
            type=irrigator_type,
            config=json.dumps(config),
        )
        self.session.add(irrigator)
        self.session.flush()
        return irrigator.id

    def get_irrigator(self, irrigator_id: int) -> Irrigator | None:
        """Get an irrigator by ID."""
        return self.session.get(Irrigator, irrigator_id)

    def get_irrigators_in_cluster(self, cluster_id: int) -> list[Irrigator]:
        """Get all irrigators in a cluster ordered by name."""
        return list(
            self.session.scalars(select(Irrigator).where(Irrigator.cluster_id == cluster_id).order_by(Irrigator.name))
        )

    # ── Sensors ───────────────────────────────────────────────────────────────

    def add_sensor(
        self,
        cluster_id: int,
        tuya_device_id: str,
        name: str,
        sensor_type: str,
        config: dict,
        plant_id: int | None = None,
    ) -> int:
        """Add a sensor device and return its ID."""
        sensor = Sensor(
            cluster_id=cluster_id,
            tuya_device_id=tuya_device_id,
            name=name,
            type=sensor_type,
            config=json.dumps(config),
            plant_id=plant_id,
        )
        self.session.add(sensor)
        self.session.flush()
        return sensor.id

    def get_sensors_in_cluster(self, cluster_id: int) -> list[Sensor]:
        """Get all sensors in a cluster ordered by name."""
        return list(self.session.scalars(select(Sensor).where(Sensor.cluster_id == cluster_id).order_by(Sensor.name)))

    # ── Sensor Readings ───────────────────────────────────────────────────────

    def add_sensor_reading(
        self,
        sensor_id: int,
        timestamp: int | None = None,
        temperature: float | None = None,
        soil_moisture: float | None = None,
        light: int | None = None,
        env_humidity: float | None = None,
        battery_state: str | None = None,
        water_warning: bool | None = None,
    ) -> int | None:
        """Add a sensor reading. Deduplicates by (sensor_id, timestamp).

        Returns row ID if inserted, None if duplicate skipped.
        """
        ts = timestamp or int(time.time())
        stmt = (
            sqlite_insert(SensorReading)
            .values(
                sensor_id=sensor_id,
                timestamp=ts,
                temperature=temperature,
                soil_moisture=soil_moisture,
                light=light,
                env_humidity=env_humidity,
                battery_state=battery_state,
                water_warning=water_warning,
            )
            .on_conflict_do_nothing(index_elements=["sensor_id", "timestamp"])
        )
        result = self.session.execute(stmt)
        self.session.flush()
        if result.rowcount > 0:
            # Fetch the inserted row's ID
            row = self.session.execute(
                select(SensorReading.id).where(SensorReading.sensor_id == sensor_id, SensorReading.timestamp == ts)
            ).scalar_one()
            return row
        return None

    def get_last_reading_timestamp(self, sensor_id: int) -> int | None:
        """Get timestamp of the most recent reading for a sensor."""
        return self.session.scalar(
            select(func.max(SensorReading.timestamp)).where(SensorReading.sensor_id == sensor_id)
        )

    def bulk_add_sensor_readings(
        self,
        readings: list[
            tuple[
                int,
                int,
                float | None,
                float | None,
                int | None,
                float | None,
                str | None,
                int | None,
            ]
        ],
    ) -> int:
        """Bulk insert sensor readings with dedup. Returns count of new rows inserted.

        Each tuple: (sensor_id, timestamp, temperature, soil_moisture, light,
                     env_humidity, battery_state, water_warning)
        """
        if not readings:
            return 0
        inserted = 0
        for r in readings:
            stmt = (
                sqlite_insert(SensorReading)
                .values(
                    sensor_id=r[0],
                    timestamp=r[1],
                    temperature=r[2],
                    soil_moisture=r[3],
                    light=r[4],
                    env_humidity=r[5],
                    battery_state=r[6],
                    water_warning=r[7],
                )
                .on_conflict_do_nothing(index_elements=["sensor_id", "timestamp"])
            )
            result = self.session.execute(stmt)
            inserted += result.rowcount
        self.session.flush()
        return inserted

    def get_recent_readings(self, sensor_id: int, hours: int = 24) -> list[SensorReading]:
        """Get recent readings for a sensor, ordered by timestamp DESC."""
        cutoff = int(time.time()) - (hours * 3600)
        return list(
            self.session.scalars(
                select(SensorReading)
                .where(SensorReading.sensor_id == sensor_id, SensorReading.timestamp >= cutoff)
                .order_by(SensorReading.timestamp.desc())
            )
        )

    def get_readings_around(
        self, sensor_id: int, timestamp: int, before_seconds: int = 1800, after_seconds: int = 7200
    ) -> tuple[list[SensorReading], list[SensorReading]]:
        """Get readings before and after a timestamp.

        Returns (before_readings, after_readings), each ordered by timestamp ASC.
        """
        before = list(
            self.session.scalars(
                select(SensorReading)
                .where(
                    SensorReading.sensor_id == sensor_id,
                    SensorReading.timestamp >= timestamp - before_seconds,
                    SensorReading.timestamp <= timestamp,
                )
                .order_by(SensorReading.timestamp.asc())
            )
        )
        after = list(
            self.session.scalars(
                select(SensorReading)
                .where(
                    SensorReading.sensor_id == sensor_id,
                    SensorReading.timestamp >= timestamp,
                    SensorReading.timestamp <= timestamp + after_seconds,
                )
                .order_by(SensorReading.timestamp.asc())
            )
        )
        return before, after

    # ── Irrigation Events ─────────────────────────────────────────────────────

    def add_irrigation_event(
        self,
        irrigator_id: int,
        action: str,
        triggered_by: str,
        duration_minutes: int | None = None,
        notes: str | None = None,
        timestamp: int | None = None,
    ) -> int:
        """Log an irrigation event and return its ID."""
        ts = timestamp or int(time.time())
        event = IrrigationEvent(
            irrigator_id=irrigator_id,
            timestamp=ts,
            action=action,
            duration_minutes=duration_minutes,
            triggered_by=triggered_by,
            notes=notes,
        )
        self.session.add(event)
        self.session.flush()
        return event.id

    def get_recent_events(self, irrigator_id: int, hours: int = 24) -> list[IrrigationEvent]:
        """Get recent events for an irrigator, ordered by timestamp DESC."""
        cutoff = int(time.time()) - (hours * 3600)
        return list(
            self.session.scalars(
                select(IrrigationEvent)
                .where(IrrigationEvent.irrigator_id == irrigator_id, IrrigationEvent.timestamp >= cutoff)
                .order_by(IrrigationEvent.timestamp.desc())
            )
        )

    # ── Irrigation Configs ────────────────────────────────────────────────────

    def set_irrigation_config(
        self,
        cluster_id: int,
        mode: str,
        duration_minutes: int | None = None,
        interval_hours: int | None = None,
        auto_run: bool = True,
    ) -> int:
        """Set or update irrigation config for a cluster and return its ID."""
        existing = self.session.scalar(select(IrrigationConfig).where(IrrigationConfig.cluster_id == cluster_id))
        if existing:
            existing.mode = mode
            existing.duration_minutes = duration_minutes
            existing.interval_hours = interval_hours
            existing.auto_run = auto_run
            existing.last_updated = int(time.time())
            self.session.flush()
            return existing.id

        config = IrrigationConfig(
            cluster_id=cluster_id,
            mode=mode,
            duration_minutes=duration_minutes,
            interval_hours=interval_hours,
            auto_run=int(auto_run),
            last_updated=int(time.time()),
        )
        self.session.add(config)
        self.session.flush()
        return config.id

    def get_irrigation_config(self, cluster_id: int) -> IrrigationConfig | None:
        """Get irrigation config for a cluster."""
        return self.session.scalar(select(IrrigationConfig).where(IrrigationConfig.cluster_id == cluster_id))
