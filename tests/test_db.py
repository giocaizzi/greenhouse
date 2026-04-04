"""Test suite for irrigation system - Database operations."""

import time

import pytest

from fake_data import (
    FAKE_CLUSTER_LOCATION,
    FAKE_CLUSTER_NAME,
    FAKE_DEVICE_ID,
    FAKE_IRRIGATOR_NAME,
    FAKE_PLANT_SPECIES,
    FAKE_SENSOR_ID,
    FAKE_SENSOR_NAME,
)


class TestDatabase:
    """Test database operations and constraints."""

    def test_cluster_creation(self, tmp_db):
        """Cluster can be created and retrieved."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME, FAKE_CLUSTER_LOCATION)
        cluster = tmp_db.get_cluster(cluster_id)

        assert cluster is not None
        assert cluster.name == FAKE_CLUSTER_NAME
        assert cluster.location == FAKE_CLUSTER_LOCATION
        assert cluster.id == cluster_id

    def test_cluster_list(self, tmp_db):
        """Multiple clusters can be listed."""
        tmp_db.add_cluster("Cluster A")
        tmp_db.add_cluster("Cluster B")
        tmp_db.add_cluster("Cluster C")

        clusters = tmp_db.list_clusters()
        assert len(clusters) == 3
        names = [c.name for c in clusters]
        assert "Cluster A" in names
        assert "Cluster B" in names
        assert "Cluster C" in names

    def test_plant_creation(self, tmp_db):
        """Plants can be added to a cluster."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        tmp_db.add_plant(
            cluster_id=cluster_id,
            species=FAKE_PLANT_SPECIES,
            category="tropical",
            water_needs="medium",
            ideal_temp_min=18.0,
            ideal_temp_max=27.0,
        )

        plants = tmp_db.get_plants_in_cluster(cluster_id)
        assert len(plants) == 1
        assert plants[0].species == FAKE_PLANT_SPECIES
        assert plants[0].water_needs == "medium"

    def test_irrigator_creation(self, tmp_db):
        """Irrigators can be added to a cluster."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        irrigator_id = tmp_db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name=FAKE_IRRIGATOR_NAME,
            irrigator_type="tuya_cloud",
            config={"interval": 12},
        )

        irrigator = tmp_db.get_irrigator(irrigator_id)
        assert irrigator is not None
        assert irrigator.name == FAKE_IRRIGATOR_NAME
        assert irrigator.tuya_device_id == FAKE_DEVICE_ID

    def test_unique_device_constraint(self, tmp_db):
        """Same Tuya device ID cannot be added twice."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        tmp_db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name="First",
            irrigator_type="tuya_cloud",
            config={},
        )

        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.add_irrigator(
                cluster_id=cluster_id,
                tuya_device_id=FAKE_DEVICE_ID,
                name="Second",
                irrigator_type="tuya_cloud",
                config={},
            )

    def test_sensor_readings(self, tmp_db):
        """Sensor readings can be logged and retrieved."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        sensor_id = tmp_db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name=FAKE_SENSOR_NAME,
            sensor_type="temp_humidity",
            config={},
        )

        now = int(time.time())
        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=now, temperature=22.5, env_humidity=65.0)
        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=now + 60, temperature=23.0, env_humidity=64.0)

        readings = tmp_db.get_recent_readings(sensor_id, hours=24)
        assert len(readings) == 2
        temps = [r.temperature for r in readings]
        assert 22.5 in temps
        assert 23.0 in temps

    def test_irrigation_events(self, tmp_db):
        """Irrigation events can be logged and retrieved."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        irrigator_id = tmp_db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name=FAKE_IRRIGATOR_NAME,
            irrigator_type="tuya_cloud",
            config={},
        )

        tmp_db.add_irrigation_event(
            irrigator_id=irrigator_id, action="start", triggered_by="manual", duration_minutes=5
        )
        tmp_db.add_irrigation_event(irrigator_id=irrigator_id, action="stop", triggered_by="manual")

        events = tmp_db.get_recent_events(irrigator_id, hours=24)
        assert len(events) == 2
        actions = [e.action for e in events]
        assert "start" in actions
        assert "stop" in actions

    def test_irrigation_config(self, tmp_db):
        """Irrigation config can be set and retrieved."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        tmp_db.set_irrigation_config(
            cluster_id=cluster_id,
            mode="smart",
            duration_minutes=3,
            interval_hours=8,
            auto_run=True,
        )

        config = tmp_db.get_irrigation_config(cluster_id)
        assert config is not None
        assert config.mode == "smart"
        assert config.duration_minutes == 3
        assert config.interval_hours == 8
        assert config.auto_run is True

    def test_config_update(self, tmp_db):
        """Irrigation config can be updated in-place."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        tmp_db.set_irrigation_config(
            cluster_id=cluster_id, mode="manual", duration_minutes=2, interval_hours=12, auto_run=False
        )
        tmp_db.set_irrigation_config(
            cluster_id=cluster_id, mode="smart", duration_minutes=5, interval_hours=6, auto_run=True
        )

        config = tmp_db.get_irrigation_config(cluster_id)
        assert config.mode == "smart"
        assert config.duration_minutes == 5

    def test_get_readings_around(self, tmp_db):
        """Readings before and after a timestamp are correctly split."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        sensor_id = tmp_db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name=FAKE_SENSOR_NAME,
            sensor_type="soil_moisture",
            config={},
        )

        pivot = 100000
        # 3 readings before, 2 after
        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=pivot - 1800, soil_moisture=30.0)
        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=pivot - 900, soil_moisture=32.0)
        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=pivot - 100, soil_moisture=33.0)
        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=pivot + 600, soil_moisture=40.0)
        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=pivot + 3600, soil_moisture=45.0)
        # Outside window (should not appear)
        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=pivot - 5000, soil_moisture=20.0)
        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=pivot + 10000, soil_moisture=50.0)

        before, after = tmp_db.get_readings_around(sensor_id, pivot, before_seconds=1800, after_seconds=7200)

        assert len(before) == 3
        assert len(after) == 2
        assert before[0].soil_moisture == pytest.approx(30.0)
        assert before[-1].soil_moisture == pytest.approx(33.0)
        assert after[0].soil_moisture == pytest.approx(40.0)

    def test_bulk_add_deduplicates(self, tmp_db):
        """Bulk insert skips duplicates and returns correct count."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        sensor_id = tmp_db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name=FAKE_SENSOR_NAME,
            sensor_type="soil_moisture",
            config={},
        )

        # Insert initial reading
        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=1000, soil_moisture=30.0)

        # Bulk insert: 1 duplicate + 2 new (8-field tuples)
        readings = [
            (sensor_id, 1000, None, 30.0, None, None, None, None),  # Duplicate
            (sensor_id, 2000, None, 35.0, None, None, None, None),  # New
            (sensor_id, 3000, None, 40.0, None, None, None, None),  # New
        ]
        inserted = tmp_db.bulk_add_sensor_readings(readings)

        assert inserted == 2
        all_readings = tmp_db.get_recent_readings(sensor_id, hours=999999)
        assert len(all_readings) == 3

    def test_bulk_add_with_extended_columns(self, tmp_db):
        """Bulk insert correctly stores env_humidity, battery_state, water_warning."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        sensor_id = tmp_db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name=FAKE_SENSOR_NAME,
            sensor_type="soil_moisture",
            config={},
        )

        readings = [
            (sensor_id, 1000, 22.5, 45.0, 800, 65.0, "high", 0),
            (sensor_id, 2000, 23.0, 50.0, 900, 60.0, "middle", 1),
        ]
        inserted = tmp_db.bulk_add_sensor_readings(readings)
        assert inserted == 2

        all_readings = tmp_db.get_recent_readings(sensor_id, hours=999999)
        assert len(all_readings) == 2
        # Check extended columns are persisted
        r = sorted(all_readings, key=lambda x: x.timestamp)
        assert r[0].env_humidity == pytest.approx(65.0)
        assert r[0].battery_state == "high"
        assert r[1].water_warning is True

    def test_cluster_environment(self, tmp_db):
        """Cluster environment field works correctly."""
        indoor_id = tmp_db.add_cluster("Indoor", environment="indoor")
        outdoor_id = tmp_db.add_cluster("Outdoor", environment="outdoor")

        indoor = tmp_db.get_cluster(indoor_id)
        outdoor = tmp_db.get_cluster(outdoor_id)

        assert indoor.environment == "indoor"
        assert outdoor.environment == "outdoor"

        # Default is indoor
        default_id = tmp_db.add_cluster("Default")
        default = tmp_db.get_cluster(default_id)
        assert default.environment == "indoor"

    def test_get_last_reading_timestamp(self, tmp_db):
        """Last reading timestamp returns most recent reading time."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        sensor_id = tmp_db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name=FAKE_SENSOR_NAME,
            sensor_type="soil_moisture",
            config={},
        )

        # No readings → None
        assert tmp_db.get_last_reading_timestamp(sensor_id) is None

        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=1000, soil_moisture=30.0)
        tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=2000, soil_moisture=35.0)

        assert tmp_db.get_last_reading_timestamp(sensor_id) == 2000

    def test_sensor_reading_dedup(self, tmp_db):
        """Duplicate (sensor_id, timestamp) is silently skipped."""
        cluster_id = tmp_db.add_cluster(FAKE_CLUSTER_NAME)
        sensor_id = tmp_db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name=FAKE_SENSOR_NAME,
            sensor_type="soil_moisture",
            config={},
        )

        result1 = tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=1000, soil_moisture=30.0)
        result2 = tmp_db.add_sensor_reading(sensor_id=sensor_id, timestamp=1000, soil_moisture=99.0)

        assert result1 is not None
        assert result2 is None  # Duplicate skipped

        readings = tmp_db.get_recent_readings(sensor_id, hours=999999)
        assert len(readings) == 1
        assert readings[0].soil_moisture == pytest.approx(30.0)  # Original value kept

    def test_nonexistent_cluster_returns_none(self, tmp_db):
        """Getting a nonexistent cluster returns None."""
        assert tmp_db.get_cluster(99999) is None
