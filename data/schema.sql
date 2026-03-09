-- Schema for irrigation.db
-- Generated from db.py initialization

-- Clusters (plant groupings)
CREATE TABLE clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT,
    created_at INTEGER NOT NULL,
    environment TEXT DEFAULT 'indoor'  -- "indoor" or "outdoor"
);

-- Plants in clusters
CREATE TABLE plants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL,
    species TEXT NOT NULL,
    category TEXT,
    water_needs TEXT,               -- "low", "medium", "high"
    light_needs TEXT,               -- "low", "medium", "high"
    ideal_temp_min REAL,            -- °C
    ideal_temp_max REAL,            -- °C
    ideal_humidity_min REAL,        -- %
    ideal_humidity_max REAL,        -- %
    notes TEXT,
    FOREIGN KEY (cluster_id) REFERENCES clusters(id)
);

-- Irrigator devices
CREATE TABLE irrigators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL,
    tuya_device_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    type TEXT NOT NULL,             -- "tuya_cloud", "tuya_local"
    config TEXT,                    -- JSON: {device_ip, local_key, interval_hours, ...}
    FOREIGN KEY (cluster_id) REFERENCES clusters(id)
);

-- Sensor devices (optional)
CREATE TABLE sensors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL,
    tuya_device_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    type TEXT NOT NULL,             -- "temp_humidity", "soil_moisture", "light"
    config TEXT,                    -- JSON: {device_ip, local_key, ...}
    plant_id INTEGER,              -- Link to specific plant (optional)
    FOREIGN KEY (cluster_id) REFERENCES clusters(id),
    FOREIGN KEY (plant_id) REFERENCES plants(id)
);

-- Sensor readings (time-series)
CREATE TABLE sensor_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_id INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    temperature REAL,               -- °C
    humidity REAL,                  -- %
    soil_moisture REAL,             -- %
    light INTEGER,                  -- lux
    FOREIGN KEY (sensor_id) REFERENCES sensors(id)
);

-- Irrigation events log
CREATE TABLE irrigation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    irrigator_id INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    action TEXT NOT NULL,           -- "start", "stop", "on", "off", "schedule_updated"
    duration_minutes INTEGER,
    triggered_by TEXT NOT NULL,     -- "manual", "auto", "schedule"
    notes TEXT,
    FOREIGN KEY (irrigator_id) REFERENCES irrigators(id)
);

-- Irrigation configuration per cluster
CREATE TABLE irrigation_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL UNIQUE,
    mode TEXT NOT NULL,             -- "manual", "schedule", "smart"
    duration_minutes INTEGER,
    interval_hours INTEGER,
    auto_run INTEGER NOT NULL,      -- boolean (0 or 1)
    last_updated INTEGER NOT NULL,
    FOREIGN KEY (cluster_id) REFERENCES clusters(id)
);

-- Performance indices
CREATE INDEX idx_sensor_readings_timestamp ON sensor_readings(timestamp);
CREATE INDEX idx_sensor_readings_sensor_id ON sensor_readings(sensor_id);
CREATE INDEX idx_irrigation_events_timestamp ON irrigation_events(timestamp);
CREATE INDEX idx_irrigation_events_irrigator_id ON irrigation_events(irrigator_id);
