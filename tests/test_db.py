#!/usr/bin/env python3
"""Test suite for irrigation system - Database operations."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fake_data import (
    FAKE_CLUSTER_LOCATION,
    FAKE_CLUSTER_NAME,
    FAKE_DEVICE_ID,
    FAKE_IRRIGATOR_NAME,
    FAKE_PLANT_SPECIES,
    FAKE_SENSOR_ID,
    FAKE_SENSOR_NAME,
)
from tuya_irrigation.db import IrrigationDB


class TestDatabase(unittest.TestCase):
    """Test database operations and constraints."""

    def setUp(self):
        """Create temporary database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.db = IrrigationDB(Path(self.temp_db.name))

    def tearDown(self):
        """Clean up temporary database."""
        self.db.close()
        Path(self.temp_db.name).unlink()

    def test_cluster_creation(self):
        """Cluster can be created and retrieved."""
        cluster_id = self.db.add_cluster(FAKE_CLUSTER_NAME, FAKE_CLUSTER_LOCATION)
        cluster = self.db.get_cluster(cluster_id)

        self.assertIsNotNone(cluster)
        self.assertEqual(cluster.name, FAKE_CLUSTER_NAME)
        self.assertEqual(cluster.location, FAKE_CLUSTER_LOCATION)
        self.assertEqual(cluster.id, cluster_id)

    def test_cluster_list(self):
        """Multiple clusters can be listed."""
        self.db.add_cluster("Cluster A")
        self.db.add_cluster("Cluster B")
        self.db.add_cluster("Cluster C")

        clusters = self.db.list_clusters()
        self.assertEqual(len(clusters), 3)
        names = [c.name for c in clusters]
        self.assertIn("Cluster A", names)
        self.assertIn("Cluster B", names)
        self.assertIn("Cluster C", names)

    def test_plant_creation(self):
        """Plants can be added to a cluster."""
        cluster_id = self.db.add_cluster(FAKE_CLUSTER_NAME)
        self.db.add_plant(
            cluster_id=cluster_id,
            species=FAKE_PLANT_SPECIES,
            category="tropical",
            water_needs="medium",
            ideal_temp_min=18.0,
            ideal_temp_max=27.0,
        )

        plants = self.db.get_plants_in_cluster(cluster_id)
        self.assertEqual(len(plants), 1)
        self.assertEqual(plants[0].species, FAKE_PLANT_SPECIES)
        self.assertEqual(plants[0].water_needs, "medium")

    def test_irrigator_creation(self):
        """Irrigators can be added to a cluster."""
        cluster_id = self.db.add_cluster(FAKE_CLUSTER_NAME)
        irrigator_id = self.db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name=FAKE_IRRIGATOR_NAME,
            irrigator_type="tuya_cloud",
            config={"interval": 12},
        )

        irrigator = self.db.get_irrigator(irrigator_id)
        self.assertIsNotNone(irrigator)
        self.assertEqual(irrigator.name, FAKE_IRRIGATOR_NAME)
        self.assertEqual(irrigator.tuya_device_id, FAKE_DEVICE_ID)

    def test_unique_device_constraint(self):
        """Same Tuya device ID cannot be added twice."""
        cluster_id = self.db.add_cluster(FAKE_CLUSTER_NAME)
        self.db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name="First",
            irrigator_type="tuya_cloud",
            config={},
        )

        with self.assertRaises(Exception):  # noqa: B017 - SQLite UNIQUE constraint raises generic Exception
            self.db.add_irrigator(
                cluster_id=cluster_id,
                tuya_device_id=FAKE_DEVICE_ID,  # same ID — should fail
                name="Second",
                irrigator_type="tuya_cloud",
                config={},
            )

    def test_sensor_readings(self):
        """Sensor readings can be logged and retrieved."""
        cluster_id = self.db.add_cluster(FAKE_CLUSTER_NAME)
        sensor_id = self.db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name=FAKE_SENSOR_NAME,
            sensor_type="temp_humidity",
            config={},
        )

        import time
        now = int(time.time())
        self.db.add_sensor_reading(sensor_id=sensor_id, timestamp=now, temperature=22.5, humidity=65.0)
        self.db.add_sensor_reading(sensor_id=sensor_id, timestamp=now + 60, temperature=23.0, humidity=64.0)

        readings = self.db.get_recent_readings(sensor_id, hours=24)
        self.assertEqual(len(readings), 2)
        temps = [r.temperature for r in readings]
        self.assertIn(22.5, temps)
        self.assertIn(23.0, temps)

    def test_irrigation_events(self):
        """Irrigation events can be logged and retrieved."""
        cluster_id = self.db.add_cluster(FAKE_CLUSTER_NAME)
        irrigator_id = self.db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name=FAKE_IRRIGATOR_NAME,
            irrigator_type="tuya_cloud",
            config={},
        )

        self.db.add_irrigation_event(irrigator_id=irrigator_id, action="start", triggered_by="manual", duration_minutes=5)
        self.db.add_irrigation_event(irrigator_id=irrigator_id, action="stop", triggered_by="manual")

        events = self.db.get_recent_events(irrigator_id, hours=24)
        self.assertEqual(len(events), 2)
        actions = [e.action for e in events]
        self.assertIn("start", actions)
        self.assertIn("stop", actions)

    def test_irrigation_config(self):
        """Irrigation config can be set and retrieved."""
        cluster_id = self.db.add_cluster(FAKE_CLUSTER_NAME)
        self.db.set_irrigation_config(
            cluster_id=cluster_id,
            mode="smart",
            duration_minutes=3,
            interval_hours=8,
            auto_run=True,
        )

        config = self.db.get_irrigation_config(cluster_id)
        self.assertIsNotNone(config)
        self.assertEqual(config.mode, "smart")
        self.assertEqual(config.duration_minutes, 3)
        self.assertEqual(config.interval_hours, 8)
        self.assertTrue(config.auto_run)

    def test_config_update(self):
        """Irrigation config can be updated in-place."""
        cluster_id = self.db.add_cluster(FAKE_CLUSTER_NAME)
        self.db.set_irrigation_config(cluster_id=cluster_id, mode="manual", duration_minutes=2, interval_hours=12, auto_run=False)
        self.db.set_irrigation_config(cluster_id=cluster_id, mode="smart", duration_minutes=5, interval_hours=6, auto_run=True)

        config = self.db.get_irrigation_config(cluster_id)
        self.assertEqual(config.mode, "smart")
        self.assertEqual(config.duration_minutes, 5)


if __name__ == "__main__":
    unittest.main()
