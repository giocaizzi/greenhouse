#!/usr/bin/env python3
"""SQLite database management for irrigation system."""

import json
import os
import sqlite3
import time
from pathlib import Path

from tuya_irrigation.models import (
    Cluster,
    IrrigationConfig,
    IrrigationEvent,
    Irrigator,
    Plant,
    Sensor,
    SensorReading,
)

_DEFAULT_DB_PATH = Path.home() / ".openclaw/workspace/skills/tuya-irrigation/data/irrigation.db"
DB_PATH = Path(os.environ["IRRIGATION_DB_PATH"]) if os.environ.get("IRRIGATION_DB_PATH") else _DEFAULT_DB_PATH


class IrrigationDB:
    """Database manager for irrigation system."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist."""
        schema = """
        -- Clusters
        CREATE TABLE IF NOT EXISTS clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT,
            created_at INTEGER NOT NULL,
            environment TEXT DEFAULT 'indoor'
        );

        -- Plants
        CREATE TABLE IF NOT EXISTS plants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id INTEGER NOT NULL,
            species TEXT NOT NULL,
            category TEXT,
            water_needs TEXT,
            light_needs TEXT,
            ideal_temp_min REAL,
            ideal_temp_max REAL,
            ideal_humidity_min REAL,
            ideal_humidity_max REAL,
            notes TEXT,
            FOREIGN KEY (cluster_id) REFERENCES clusters(id)
        );

        -- Irrigators
        CREATE TABLE IF NOT EXISTS irrigators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id INTEGER NOT NULL,
            tuya_device_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            config TEXT,
            FOREIGN KEY (cluster_id) REFERENCES clusters(id)
        );

        -- Sensors
        CREATE TABLE IF NOT EXISTS sensors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id INTEGER NOT NULL,
            tuya_device_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            config TEXT,
            plant_id INTEGER,
            FOREIGN KEY (cluster_id) REFERENCES clusters(id),
            FOREIGN KEY (plant_id) REFERENCES plants(id)
        );

        -- Sensor Readings
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id INTEGER NOT NULL,
            timestamp INTEGER NOT NULL,
            temperature REAL,
            humidity REAL,
            soil_moisture REAL,
            light INTEGER,
            FOREIGN KEY (sensor_id) REFERENCES sensors(id),
            UNIQUE (sensor_id, timestamp)
        );

        -- Irrigation Events
        CREATE TABLE IF NOT EXISTS irrigation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            irrigator_id INTEGER NOT NULL,
            timestamp INTEGER NOT NULL,
            action TEXT NOT NULL,
            duration_minutes INTEGER,
            triggered_by TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY (irrigator_id) REFERENCES irrigators(id)
        );

        -- Irrigation Configs
        CREATE TABLE IF NOT EXISTS irrigation_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id INTEGER NOT NULL UNIQUE,
            mode TEXT NOT NULL,
            duration_minutes INTEGER,
            interval_hours INTEGER,
            auto_run INTEGER NOT NULL,
            last_updated INTEGER NOT NULL,
            FOREIGN KEY (cluster_id) REFERENCES clusters(id)
        );

        -- Indices for performance
        CREATE INDEX IF NOT EXISTS idx_sensor_readings_timestamp
            ON sensor_readings(timestamp);
        CREATE INDEX IF NOT EXISTS idx_sensor_readings_sensor_id
            ON sensor_readings(sensor_id);
        CREATE INDEX IF NOT EXISTS idx_irrigation_events_timestamp
            ON irrigation_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_irrigation_events_irrigator_id
            ON irrigation_events(irrigator_id);
        """
        self.conn.executescript(schema)
        self.conn.commit()
        self._migrate_schema()

    def _migrate_schema(self):
        """Apply incremental schema migrations for existing databases."""
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(sensor_readings)").fetchall()}
        # v0.6: battery_state
        if "battery_state" not in cols:
            self.conn.execute("ALTER TABLE sensor_readings ADD COLUMN battery_state TEXT")
        # v0.7: env_humidity (DP 101), water_warning (DP 111)
        if "env_humidity" not in cols:
            self.conn.execute("ALTER TABLE sensor_readings ADD COLUMN env_humidity REAL")
        if "water_warning" not in cols:
            self.conn.execute("ALTER TABLE sensor_readings ADD COLUMN water_warning INTEGER")
        self.conn.commit()

    # ── Clusters ──────────────────────────────────────────────────────────────

    def add_cluster(self, name: str, location: str | None = None, environment: str = "indoor") -> int:
        """Add a new cluster and return its ID."""
        cursor = self.conn.execute(
            "INSERT INTO clusters (name, location, created_at, environment) VALUES (?, ?, ?, ?)",
            (name, location, int(time.time()), environment),
        )
        self.conn.commit()
        return cursor.lastrowid

    def _row_to_cluster(self, row) -> Cluster:
        return Cluster(
            id=row["id"],
            name=row["name"],
            location=row["location"],
            created_at=row["created_at"],
            environment=row["environment"] if "environment" in row.keys() else "indoor",
        )

    def get_cluster(self, cluster_id: int) -> Cluster | None:
        """Get a cluster by ID."""
        row = self.conn.execute("SELECT * FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
        if not row:
            return None
        return self._row_to_cluster(row)

    def list_clusters(self) -> list[Cluster]:
        """List all clusters."""
        rows = self.conn.execute("SELECT * FROM clusters ORDER BY name").fetchall()
        return [self._row_to_cluster(row) for row in rows]

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
        cursor = self.conn.execute(
            """INSERT INTO plants
               (cluster_id, species, category, water_needs, light_needs,
                ideal_temp_min, ideal_temp_max, ideal_humidity_min, ideal_humidity_max, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cluster_id,
                species,
                category,
                water_needs,
                light_needs,
                ideal_temp_min,
                ideal_temp_max,
                ideal_humidity_min,
                ideal_humidity_max,
                notes,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_plants_in_cluster(self, cluster_id: int) -> list[Plant]:
        """Get all plants in a cluster."""
        rows = self.conn.execute("SELECT * FROM plants WHERE cluster_id = ? ORDER BY species", (cluster_id,)).fetchall()
        return [self._row_to_plant(row) for row in rows]

    def _row_to_plant(self, row) -> Plant:
        return Plant(
            id=row["id"],
            cluster_id=row["cluster_id"],
            species=row["species"],
            category=row["category"],
            water_needs=row["water_needs"],
            light_needs=row["light_needs"],
            ideal_temp_min=row["ideal_temp_min"],
            ideal_temp_max=row["ideal_temp_max"],
            ideal_humidity_min=row["ideal_humidity_min"],
            ideal_humidity_max=row["ideal_humidity_max"],
            notes=row["notes"],
        )

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
        cursor = self.conn.execute(
            "INSERT INTO irrigators (cluster_id, tuya_device_id, name, type, config) VALUES (?, ?, ?, ?, ?)",
            (cluster_id, tuya_device_id, name, irrigator_type, json.dumps(config)),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_irrigator(self, irrigator_id: int) -> Irrigator | None:
        """Get an irrigator by ID."""
        row = self.conn.execute("SELECT * FROM irrigators WHERE id = ?", (irrigator_id,)).fetchone()
        if not row:
            return None
        return self._row_to_irrigator(row)

    def get_irrigators_in_cluster(self, cluster_id: int) -> list[Irrigator]:
        """Get all irrigators in a cluster."""
        rows = self.conn.execute(
            "SELECT * FROM irrigators WHERE cluster_id = ? ORDER BY name", (cluster_id,)
        ).fetchall()
        return [self._row_to_irrigator(row) for row in rows]

    def _row_to_irrigator(self, row) -> Irrigator:
        return Irrigator(
            id=row["id"],
            cluster_id=row["cluster_id"],
            tuya_device_id=row["tuya_device_id"],
            name=row["name"],
            type=row["type"],
            config=row["config"],
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
        cursor = self.conn.execute(
            "INSERT INTO sensors (cluster_id, tuya_device_id, name, type, config, plant_id) VALUES (?, ?, ?, ?, ?, ?)",
            (cluster_id, tuya_device_id, name, sensor_type, json.dumps(config), plant_id),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_sensors_in_cluster(self, cluster_id: int) -> list[Sensor]:
        """Get all sensors in a cluster."""
        rows = self.conn.execute("SELECT * FROM sensors WHERE cluster_id = ? ORDER BY name", (cluster_id,)).fetchall()
        return [self._row_to_sensor(row) for row in rows]

    def _row_to_sensor(self, row) -> Sensor:
        return Sensor(
            id=row["id"],
            cluster_id=row["cluster_id"],
            tuya_device_id=row["tuya_device_id"],
            name=row["name"],
            type=row["type"],
            config=row["config"],
            plant_id=row["plant_id"] if "plant_id" in row.keys() else None,
        )

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
        ww = int(water_warning) if water_warning is not None else None
        cursor = self.conn.execute(
            """INSERT OR IGNORE INTO sensor_readings
               (sensor_id, timestamp, temperature, soil_moisture, light,
                env_humidity, battery_state, water_warning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sensor_id, ts, temperature, soil_moisture, light, env_humidity, battery_state, ww),
        )
        self.conn.commit()
        return cursor.lastrowid if cursor.rowcount > 0 else None

    def get_last_reading_timestamp(self, sensor_id: int) -> int | None:
        """Get timestamp of the most recent reading for a sensor."""
        row = self.conn.execute(
            "SELECT MAX(timestamp) FROM sensor_readings WHERE sensor_id = ?",
            (sensor_id,),
        ).fetchone()
        return row[0] if row and row[0] else None

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
        before = self.conn.total_changes
        self.conn.executemany(
            """INSERT OR IGNORE INTO sensor_readings
               (sensor_id, timestamp, temperature, soil_moisture, light,
                env_humidity, battery_state, water_warning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            readings,
        )
        inserted = self.conn.total_changes - before
        self.conn.commit()
        return inserted

    def get_recent_readings(self, sensor_id: int, hours: int = 24) -> list[SensorReading]:
        """Get recent readings for a sensor."""
        cutoff = int(time.time()) - (hours * 3600)
        rows = self.conn.execute(
            "SELECT * FROM sensor_readings WHERE sensor_id = ? AND timestamp >= ? ORDER BY timestamp DESC",
            (sensor_id, cutoff),
        ).fetchall()
        return [self._row_to_reading(row) for row in rows]

    def _row_to_reading(self, row) -> SensorReading:
        keys = row.keys()
        ww_raw = row["water_warning"] if "water_warning" in keys else None
        return SensorReading(
            id=row["id"],
            sensor_id=row["sensor_id"],
            timestamp=row["timestamp"],
            temperature=row["temperature"],
            soil_moisture=row["soil_moisture"],
            light=row["light"] if "light" in keys else None,
            env_humidity=row["env_humidity"] if "env_humidity" in keys else None,
            battery_state=row["battery_state"] if "battery_state" in keys else None,
            water_warning=bool(ww_raw) if ww_raw is not None else None,
        )

    def get_readings_around(
        self, sensor_id: int, timestamp: int, before_seconds: int = 1800, after_seconds: int = 7200
    ) -> tuple[list[SensorReading], list[SensorReading]]:
        """Get readings before and after a timestamp.

        Returns (before_readings, after_readings), each ordered by timestamp ASC.
        """
        before = self.conn.execute(
            "SELECT * FROM sensor_readings WHERE sensor_id = ? AND timestamp BETWEEN ? AND ? ORDER BY timestamp ASC",
            (sensor_id, timestamp - before_seconds, timestamp),
        ).fetchall()
        after = self.conn.execute(
            "SELECT * FROM sensor_readings WHERE sensor_id = ? AND timestamp BETWEEN ? AND ? ORDER BY timestamp ASC",
            (sensor_id, timestamp, timestamp + after_seconds),
        ).fetchall()
        return (
            [self._row_to_reading(r) for r in before],
            [self._row_to_reading(r) for r in after],
        )

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
        cursor = self.conn.execute(
            """INSERT INTO irrigation_events
               (irrigator_id, timestamp, action, duration_minutes, triggered_by, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (irrigator_id, ts, action, duration_minutes, triggered_by, notes),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_recent_events(self, irrigator_id: int, hours: int = 24) -> list[IrrigationEvent]:
        """Get recent events for an irrigator."""
        cutoff = int(time.time()) - (hours * 3600)
        rows = self.conn.execute(
            "SELECT * FROM irrigation_events WHERE irrigator_id = ? AND timestamp >= ? ORDER BY timestamp DESC",
            (irrigator_id, cutoff),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row) -> IrrigationEvent:
        return IrrigationEvent(
            id=row["id"],
            irrigator_id=row["irrigator_id"],
            timestamp=row["timestamp"],
            action=row["action"],
            duration_minutes=row["duration_minutes"],
            triggered_by=row["triggered_by"],
            notes=row["notes"],
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
        # Try update first
        cursor = self.conn.execute(
            """UPDATE irrigation_configs
               SET mode = ?, duration_minutes = ?, interval_hours = ?, auto_run = ?, last_updated = ?
               WHERE cluster_id = ?""",
            (mode, duration_minutes, interval_hours, int(auto_run), int(time.time()), cluster_id),
        )
        if cursor.rowcount > 0:
            self.conn.commit()
            row = self.conn.execute("SELECT id FROM irrigation_configs WHERE cluster_id = ?", (cluster_id,)).fetchone()
            return row["id"]

        # Insert if not exists
        cursor = self.conn.execute(
            """INSERT INTO irrigation_configs
               (cluster_id, mode, duration_minutes, interval_hours, auto_run, last_updated)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cluster_id, mode, duration_minutes, interval_hours, int(auto_run), int(time.time())),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_irrigation_config(self, cluster_id: int) -> IrrigationConfig | None:
        """Get irrigation config for a cluster."""
        row = self.conn.execute("SELECT * FROM irrigation_configs WHERE cluster_id = ?", (cluster_id,)).fetchone()
        if not row:
            return None
        return IrrigationConfig(
            id=row["id"],
            cluster_id=row["cluster_id"],
            mode=row["mode"],
            duration_minutes=row["duration_minutes"],
            interval_hours=row["interval_hours"],
            auto_run=bool(row["auto_run"]),
            last_updated=row["last_updated"],
        )

    def close(self):
        """Close database connection."""
        self.conn.close()
