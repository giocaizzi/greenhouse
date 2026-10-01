CREATE TABLE activity_events (
	id INTEGER NOT NULL,
	timestamp INTEGER NOT NULL,
	source VARCHAR NOT NULL,
	entity_type VARCHAR NOT NULL,
	entity_id INTEGER,
	severity VARCHAR NOT NULL,
	code VARCHAR NOT NULL,
	message VARCHAR NOT NULL,
	payload_json VARCHAR,
	PRIMARY KEY (id)
)


CREATE INDEX idx_activity_events_entity ON activity_events (entity_type, entity_id)
CREATE INDEX idx_activity_events_source ON activity_events (source)
CREATE INDEX idx_activity_events_timestamp ON activity_events (timestamp)

CREATE TABLE alerts (
	id INTEGER NOT NULL,
	dedup_key VARCHAR NOT NULL,
	source VARCHAR NOT NULL,
	code VARCHAR NOT NULL,
	severity VARCHAR NOT NULL,
	entity_type VARCHAR NOT NULL,
	entity_id INTEGER,
	cluster_id INTEGER,
	plant_id INTEGER,
	title VARCHAR NOT NULL,
	message VARCHAR NOT NULL,
	payload_json VARCHAR,
	status VARCHAR NOT NULL,
	first_seen_at INTEGER NOT NULL,
	last_seen_at INTEGER NOT NULL,
	occurrence_count INTEGER NOT NULL,
	acknowledged_at INTEGER,
	resolved_at INTEGER,
	PRIMARY KEY (id),
	UNIQUE (dedup_key)
)


CREATE INDEX idx_alerts_dedup_key ON alerts (dedup_key)
CREATE INDEX idx_alerts_entity ON alerts (entity_type, entity_id)
CREATE INDEX idx_alerts_status ON alerts (status)

CREATE TABLE clusters (
	id INTEGER NOT NULL,
	name VARCHAR NOT NULL,
	location VARCHAR,
	created_at INTEGER NOT NULL,
	environment VARCHAR NOT NULL,
	PRIMARY KEY (id)
)



CREATE TABLE decision_logs (
	id INTEGER NOT NULL,
	cluster_id INTEGER NOT NULL,
	evaluated_at INTEGER NOT NULL,
	action VARCHAR NOT NULL,
	duration_minutes INTEGER NOT NULL,
	interval_hours INTEGER NOT NULL,
	confidence FLOAT NOT NULL,
	primary_code VARCHAR,
	reason_text VARCHAR NOT NULL,
	payload_json VARCHAR NOT NULL,
	triggered_by VARCHAR NOT NULL,
	actuated BOOLEAN NOT NULL,
	PRIMARY KEY (id)
)


CREATE INDEX idx_decision_logs_cluster_id ON decision_logs (cluster_id)
CREATE INDEX idx_decision_logs_evaluated_at ON decision_logs (evaluated_at)

CREATE TABLE global_irrigation_config (
	id INTEGER NOT NULL,
	mode VARCHAR,
	duration_minutes INTEGER,
	interval_hours INTEGER,
	auto_run BOOLEAN,
	daily_cap_minutes INTEGER,
	max_events_per_day INTEGER,
	quiet_start_hour INTEGER,
	quiet_end_hour INTEGER,
	last_updated INTEGER NOT NULL,
	PRIMARY KEY (id)
)



CREATE TABLE plant_health_daily (
	id INTEGER NOT NULL,
	plant_id INTEGER NOT NULL,
	date_key VARCHAR NOT NULL,
	timestamp INTEGER NOT NULL,
	score FLOAT NOT NULL,
	soil_in_band_pct FLOAT,
	temp_in_band_pct FLOAT,
	humidity_in_band_pct FLOAT,
	efficiency FLOAT,
	sample_count INTEGER NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (plant_id, date_key)
)


CREATE INDEX idx_plant_health_daily_plant ON plant_health_daily (plant_id)

CREATE TABLE user_preferences (
	id INTEGER NOT NULL,
	units VARCHAR NOT NULL,
	timezone VARCHAR NOT NULL,
	theme VARCHAR NOT NULL,
	default_cluster_id INTEGER,
	refresh_interval_seconds INTEGER NOT NULL,
	dry_run_global BOOLEAN NOT NULL,
	scheduler_paused BOOLEAN NOT NULL,
	notify_manual BOOLEAN NOT NULL,
	notify_emergency BOOLEAN NOT NULL,
	notify_alerts BOOLEAN NOT NULL,
	notify_auto BOOLEAN NOT NULL,
	PRIMARY KEY (id)
)



CREATE TABLE users (
	id INTEGER NOT NULL,
	username VARCHAR NOT NULL,
	hashed_password VARCHAR NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at INTEGER NOT NULL,
	last_login_at INTEGER,
	PRIMARY KEY (id),
	UNIQUE (username)
)



CREATE TABLE vacation_windows (
	id INTEGER NOT NULL,
	starts_at INTEGER NOT NULL,
	ends_at INTEGER NOT NULL,
	contact_email VARCHAR,
	notes VARCHAR,
	created_at INTEGER NOT NULL,
	PRIMARY KEY (id)
)



CREATE TABLE irrigation_configs (
	id INTEGER NOT NULL,
	cluster_id INTEGER NOT NULL,
	mode VARCHAR,
	duration_minutes INTEGER,
	interval_hours INTEGER,
	auto_run BOOLEAN,
	last_updated INTEGER NOT NULL,
	daily_cap_minutes INTEGER,
	max_events_per_day INTEGER,
	quiet_start_hour INTEGER,
	quiet_end_hour INTEGER,
	PRIMARY KEY (id),
	UNIQUE (cluster_id),
	FOREIGN KEY(cluster_id) REFERENCES clusters (id)
)



CREATE TABLE irrigation_windows (
	id INTEGER NOT NULL,
	cluster_id INTEGER NOT NULL,
	weekday_mask INTEGER NOT NULL,
	start_hour INTEGER NOT NULL,
	end_hour INTEGER NOT NULL,
	label VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(cluster_id) REFERENCES clusters (id) ON DELETE CASCADE
)


CREATE INDEX idx_irrigation_windows_cluster ON irrigation_windows (cluster_id)

CREATE TABLE irrigators (
	id INTEGER NOT NULL,
	cluster_id INTEGER NOT NULL,
	tuya_device_id VARCHAR NOT NULL,
	name VARCHAR NOT NULL,
	type VARCHAR NOT NULL,
	config VARCHAR,
	reservoir_l FLOAT,
	flow_rate_l_per_min FLOAT,
	PRIMARY KEY (id),
	UNIQUE (cluster_id),
	FOREIGN KEY(cluster_id) REFERENCES clusters (id),
	UNIQUE (tuya_device_id)
)



CREATE TABLE plants (
	id INTEGER NOT NULL,
	cluster_id INTEGER NOT NULL,
	species VARCHAR NOT NULL,
	category VARCHAR,
	water_needs VARCHAR,
	light_needs VARCHAR,
	ideal_temp_min FLOAT,
	ideal_temp_max FLOAT,
	ideal_humidity_min FLOAT,
	ideal_humidity_max FLOAT,
	notes VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(cluster_id) REFERENCES clusters (id)
)



CREATE TABLE irrigation_events (
	id INTEGER NOT NULL,
	irrigator_id INTEGER NOT NULL,
	timestamp INTEGER NOT NULL,
	action VARCHAR NOT NULL,
	duration_minutes INTEGER,
	triggered_by VARCHAR NOT NULL,
	notes VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(irrigator_id) REFERENCES irrigators (id)
)


CREATE INDEX idx_irrigation_events_irrigator_id ON irrigation_events (irrigator_id)
CREATE INDEX idx_irrigation_events_timestamp ON irrigation_events (timestamp)

CREATE TABLE sensors (
	id INTEGER NOT NULL,
	cluster_id INTEGER NOT NULL,
	tuya_device_id VARCHAR NOT NULL,
	name VARCHAR NOT NULL,
	type VARCHAR NOT NULL,
	config VARCHAR,
	plant_id INTEGER,
	PRIMARY KEY (id),
	FOREIGN KEY(cluster_id) REFERENCES clusters (id),
	UNIQUE (tuya_device_id),
	FOREIGN KEY(plant_id) REFERENCES plants (id)
)



CREATE TABLE sensor_assignments (
	id INTEGER NOT NULL,
	sensor_id INTEGER NOT NULL,
	plant_id INTEGER NOT NULL,
	started_at INTEGER NOT NULL,
	ended_at INTEGER,
	PRIMARY KEY (id),
	FOREIGN KEY(sensor_id) REFERENCES sensors (id) ON DELETE CASCADE,
	FOREIGN KEY(plant_id) REFERENCES plants (id) ON DELETE CASCADE
)


CREATE INDEX idx_sensor_assignments_plant ON sensor_assignments (plant_id)
CREATE INDEX idx_sensor_assignments_sensor ON sensor_assignments (sensor_id)
CREATE INDEX idx_sensor_assignments_time ON sensor_assignments (started_at, ended_at)

CREATE TABLE sensor_readings (
	id INTEGER NOT NULL,
	sensor_id INTEGER NOT NULL,
	timestamp INTEGER NOT NULL,
	temperature FLOAT,
	soil_moisture FLOAT,
	light INTEGER,
	env_humidity FLOAT,
	battery_state VARCHAR,
	water_warning BOOLEAN,
	PRIMARY KEY (id),
	UNIQUE (sensor_id, timestamp),
	FOREIGN KEY(sensor_id) REFERENCES sensors (id)
)


CREATE INDEX idx_sensor_readings_sensor_id ON sensor_readings (sensor_id)
CREATE INDEX idx_sensor_readings_timestamp ON sensor_readings (timestamp)
