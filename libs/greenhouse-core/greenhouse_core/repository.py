"""Database operations for the irrigation system (replaces IrrigationDB)."""

import json
import time

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from greenhouse_core.models import (
    ActivityEvent,
    Alert,
    Cluster,
    DecisionLog,
    IrrigationConfig,
    IrrigationEvent,
    Irrigator,
    Plant,
    PlantHealthDaily,
    Sensor,
    SensorReading,
    UserPreferences,
    VacationWindow,
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
            auto_run=auto_run,
            last_updated=int(time.time()),
        )
        self.session.add(config)
        self.session.flush()
        return config.id

    def get_irrigation_config(self, cluster_id: int) -> IrrigationConfig | None:
        """Get irrigation config for a cluster."""
        return self.session.scalar(select(IrrigationConfig).where(IrrigationConfig.cluster_id == cluster_id))

    # ── Decision Log ──────────────────────────────────────────────────────────

    def add_decision_log(
        self,
        cluster_id: int,
        evaluated_at: int,
        action: str,
        duration_minutes: int,
        interval_hours: int,
        confidence: float,
        primary_code: str | None,
        reason_text: str,
        payload: dict,
        triggered_by: str = "auto",
        actuated: bool = False,
    ) -> int:
        """Persist a single decision evaluation; returns the new row id."""
        row = DecisionLog(
            cluster_id=cluster_id,
            evaluated_at=evaluated_at,
            action=action,
            duration_minutes=duration_minutes,
            interval_hours=interval_hours,
            confidence=confidence,
            primary_code=primary_code,
            reason_text=reason_text,
            payload_json=json.dumps(payload, default=str),
            triggered_by=triggered_by,
            actuated=actuated,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def list_decision_logs(self, cluster_id: int, limit: int = 50) -> list[DecisionLog]:
        """Recent decisions for a cluster, newest first."""
        return list(
            self.session.scalars(
                select(DecisionLog)
                .where(DecisionLog.cluster_id == cluster_id)
                .order_by(DecisionLog.evaluated_at.desc())
                .limit(limit)
            )
        )

    # ── Activity Events ───────────────────────────────────────────────────────

    def add_activity_event(
        self,
        source: str,
        entity_type: str,
        code: str,
        message: str,
        *,
        entity_id: int | None = None,
        severity: str = "info",
        payload: dict | None = None,
        timestamp: int | None = None,
    ) -> int:
        """Append a polymorphic activity event; returns the new row id."""
        row = ActivityEvent(
            timestamp=timestamp or int(time.time()),
            source=source,
            entity_type=entity_type,
            entity_id=entity_id,
            severity=severity,
            code=code,
            message=message,
            payload_json=json.dumps(payload, default=str) if payload else None,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def list_activity_events(
        self,
        *,
        entity_type: str | None = None,
        entity_id: int | None = None,
        source: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        before: int | None = None,
    ) -> list[ActivityEvent]:
        """List events newest-first with optional filters and pagination cursor."""
        stmt = select(ActivityEvent).order_by(ActivityEvent.timestamp.desc()).limit(limit)
        if entity_type:
            stmt = stmt.where(ActivityEvent.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(ActivityEvent.entity_id == entity_id)
        if source:
            stmt = stmt.where(ActivityEvent.source == source)
        if severity:
            stmt = stmt.where(ActivityEvent.severity == severity)
        if before is not None:
            stmt = stmt.where(ActivityEvent.timestamp < before)
        return list(self.session.scalars(stmt))

    # ── Alerts ────────────────────────────────────────────────────────────────

    def upsert_alert(
        self,
        dedup_key: str,
        source: str,
        code: str,
        title: str,
        message: str,
        *,
        severity: str = "info",
        entity_type: str = "cluster",
        entity_id: int | None = None,
        cluster_id: int | None = None,
        plant_id: int | None = None,
        payload: dict | None = None,
        seen_at: int | None = None,
    ) -> Alert:
        """Insert or refresh an alert keyed by ``dedup_key``.

        Increments occurrence_count and updates last_seen_at when an open or
        acknowledged alert with the same key already exists. A resolved alert
        with the same key is reopened.
        """
        now = seen_at or int(time.time())
        existing = self.session.scalar(select(Alert).where(Alert.dedup_key == dedup_key))
        if existing:
            existing.last_seen_at = now
            existing.occurrence_count += 1
            existing.severity = severity
            existing.message = message
            existing.title = title
            if existing.status == "resolved":
                existing.status = "open"
                existing.resolved_at = None
            self.session.flush()
            return existing
        alert = Alert(
            dedup_key=dedup_key,
            source=source,
            code=code,
            severity=severity,
            entity_type=entity_type,
            entity_id=entity_id,
            cluster_id=cluster_id,
            plant_id=plant_id,
            title=title,
            message=message,
            payload_json=json.dumps(payload, default=str) if payload else None,
            status="open",
            first_seen_at=now,
            last_seen_at=now,
            occurrence_count=1,
        )
        self.session.add(alert)
        self.session.flush()
        return alert

    def list_alerts(
        self,
        *,
        status: str | None = None,
        cluster_id: int | None = None,
        plant_id: int | None = None,
        limit: int = 100,
    ) -> list[Alert]:
        """List alerts ordered by last_seen_at desc with optional filters."""
        stmt = select(Alert).order_by(Alert.last_seen_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(Alert.status == status)
        if cluster_id is not None:
            stmt = stmt.where(Alert.cluster_id == cluster_id)
        if plant_id is not None:
            stmt = stmt.where(Alert.plant_id == plant_id)
        return list(self.session.scalars(stmt))

    def get_alert(self, alert_id: int) -> Alert | None:
        """Fetch a single alert by id."""
        return self.session.get(Alert, alert_id)

    def acknowledge_alert(self, alert_id: int) -> Alert | None:
        """Move an open alert to ``acknowledged``; returns the updated row."""
        alert = self.session.get(Alert, alert_id)
        if not alert:
            return None
        if alert.status == "open":
            alert.status = "acknowledged"
            alert.acknowledged_at = int(time.time())
            self.session.flush()
        return alert

    def resolve_alert(self, alert_id: int) -> Alert | None:
        """Mark an alert resolved; returns the updated row."""
        alert = self.session.get(Alert, alert_id)
        if not alert:
            return None
        alert.status = "resolved"
        alert.resolved_at = int(time.time())
        self.session.flush()
        return alert

    def count_open_alerts(self) -> int:
        """Total open alerts (powers the top-bar bell badge)."""
        return self.session.scalar(select(func.count()).select_from(Alert).where(Alert.status == "open")) or 0

    # ── Plant Health Daily ────────────────────────────────────────────────────

    def upsert_plant_health(
        self,
        plant_id: int,
        date_key: str,
        score: float,
        *,
        soil_in_band_pct: float | None = None,
        temp_in_band_pct: float | None = None,
        humidity_in_band_pct: float | None = None,
        efficiency: float | None = None,
        sample_count: int = 0,
        timestamp: int | None = None,
    ) -> PlantHealthDaily:
        """Idempotent upsert for the per-plant daily health snapshot."""
        existing = self.session.scalar(
            select(PlantHealthDaily).where(PlantHealthDaily.plant_id == plant_id, PlantHealthDaily.date_key == date_key)
        )
        if existing:
            existing.score = score
            existing.soil_in_band_pct = soil_in_band_pct
            existing.temp_in_band_pct = temp_in_band_pct
            existing.humidity_in_band_pct = humidity_in_band_pct
            existing.efficiency = efficiency
            existing.sample_count = sample_count
            self.session.flush()
            return existing
        row = PlantHealthDaily(
            plant_id=plant_id,
            date_key=date_key,
            timestamp=timestamp or int(time.time()),
            score=score,
            soil_in_band_pct=soil_in_band_pct,
            temp_in_band_pct=temp_in_band_pct,
            humidity_in_band_pct=humidity_in_band_pct,
            efficiency=efficiency,
            sample_count=sample_count,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_plant_health_history(self, plant_id: int, days: int = 90) -> list[PlantHealthDaily]:
        """Last ``days`` of health snapshots oldest-first for charting."""
        cutoff = int(time.time()) - days * 86400
        return list(
            self.session.scalars(
                select(PlantHealthDaily)
                .where(PlantHealthDaily.plant_id == plant_id, PlantHealthDaily.timestamp >= cutoff)
                .order_by(PlantHealthDaily.timestamp.asc())
            )
        )

    # ── Vacation Windows ──────────────────────────────────────────────────────

    def add_vacation_window(
        self,
        starts_at: int,
        ends_at: int,
        contact_email: str | None = None,
        notes: str | None = None,
    ) -> VacationWindow:
        """Create a vacation window."""
        row = VacationWindow(
            starts_at=starts_at,
            ends_at=ends_at,
            contact_email=contact_email,
            notes=notes,
            created_at=int(time.time()),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_vacation_windows(self) -> list[VacationWindow]:
        """All vacation windows ordered by start time desc."""
        return list(self.session.scalars(select(VacationWindow).order_by(VacationWindow.starts_at.desc())))

    def get_active_vacation(self, at: int | None = None) -> VacationWindow | None:
        """The currently-active vacation window, if any."""
        now = at or int(time.time())
        return self.session.scalar(
            select(VacationWindow).where(VacationWindow.starts_at <= now, VacationWindow.ends_at >= now)
        )

    def delete_vacation_window(self, window_id: int) -> bool:
        """Delete a vacation window; returns True if a row was removed."""
        row = self.session.get(VacationWindow, window_id)
        if not row:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    # ── User Preferences (single-row) ─────────────────────────────────────────

    def get_preferences(self) -> UserPreferences:
        """Return the singleton preferences row, creating defaults if missing."""
        prefs = self.session.scalar(select(UserPreferences).limit(1))
        if prefs:
            return prefs
        prefs = UserPreferences()
        self.session.add(prefs)
        self.session.flush()
        return prefs

    def update_preferences(self, **fields) -> UserPreferences:
        """Patch preferences with the provided keyword args; unknown keys are ignored."""
        prefs = self.get_preferences()
        for key, value in fields.items():
            if hasattr(prefs, key) and value is not None:
                setattr(prefs, key, value)
        self.session.flush()
        return prefs

    # ── Generic helpers used by health / quality services ─────────────────────

    def list_all_sensors(self) -> list[Sensor]:
        """All sensors regardless of cluster (used by health pulse, anomaly)."""
        return list(self.session.scalars(select(Sensor).order_by(Sensor.name)))

    def list_all_irrigators(self) -> list[Irrigator]:
        """All irrigators regardless of cluster."""
        return list(self.session.scalars(select(Irrigator).order_by(Irrigator.name)))

    def list_all_plants(self) -> list[Plant]:
        """All plants regardless of cluster."""
        return list(self.session.scalars(select(Plant).order_by(Plant.species)))

    def get_plant(self, plant_id: int) -> Plant | None:
        """Fetch a plant by id."""
        return self.session.get(Plant, plant_id)

    def get_sensor(self, sensor_id: int) -> Sensor | None:
        """Fetch a sensor by id."""
        return self.session.get(Sensor, sensor_id)

    # ── Mutations for CRUD edit/delete ────────────────────────────────────────

    def update_cluster(self, cluster_id: int, **fields) -> Cluster | None:
        """Patch cluster fields; returns the updated row or None if missing."""
        cluster = self.session.get(Cluster, cluster_id)
        if not cluster:
            return None
        for key, value in fields.items():
            if hasattr(cluster, key) and value is not None:
                setattr(cluster, key, value)
        self.session.flush()
        return cluster

    def delete_cluster(self, cluster_id: int) -> bool:
        """Delete a cluster (cascades to plants/sensors/irrigators/config)."""
        cluster = self.session.get(Cluster, cluster_id)
        if not cluster:
            return False
        self.session.delete(cluster)
        self.session.flush()
        return True

    def update_plant(self, plant_id: int, **fields) -> Plant | None:
        """Patch plant fields; returns the updated row or None."""
        plant = self.session.get(Plant, plant_id)
        if not plant:
            return None
        for key, value in fields.items():
            if hasattr(plant, key) and value is not None:
                setattr(plant, key, value)
        self.session.flush()
        return plant

    def delete_plant(self, plant_id: int) -> bool:
        """Delete a plant (sensors retain their cluster; plant_id becomes NULL)."""
        plant = self.session.get(Plant, plant_id)
        if not plant:
            return False
        for sensor in plant.sensors:
            sensor.plant_id = None
        self.session.delete(plant)
        self.session.flush()
        return True

    def update_sensor(self, sensor_id: int, **fields) -> Sensor | None:
        """Patch sensor fields; ``config`` is JSON-serialised if a dict."""
        sensor = self.session.get(Sensor, sensor_id)
        if not sensor:
            return None
        for key, value in fields.items():
            if value is None:
                continue
            if key == "config" and isinstance(value, dict):
                sensor.config = json.dumps(value)
            elif hasattr(sensor, key):
                setattr(sensor, key, value)
        self.session.flush()
        return sensor

    def delete_sensor(self, sensor_id: int) -> bool:
        """Delete a sensor (cascades to its readings)."""
        sensor = self.session.get(Sensor, sensor_id)
        if not sensor:
            return False
        self.session.delete(sensor)
        self.session.flush()
        return True

    def update_irrigator(self, irrigator_id: int, **fields) -> Irrigator | None:
        """Patch irrigator fields; ``config`` is JSON-serialised if a dict."""
        irrigator = self.session.get(Irrigator, irrigator_id)
        if not irrigator:
            return None
        for key, value in fields.items():
            if value is None:
                continue
            if key == "config" and isinstance(value, dict):
                irrigator.config = json.dumps(value)
            elif hasattr(irrigator, key):
                setattr(irrigator, key, value)
        self.session.flush()
        return irrigator

    def delete_irrigator(self, irrigator_id: int) -> bool:
        """Delete an irrigator (cascades to its events)."""
        irrigator = self.session.get(Irrigator, irrigator_id)
        if not irrigator:
            return False
        self.session.delete(irrigator)
        self.session.flush()
        return True
