#!/usr/bin/env python3
"""Test suite for irrigation learning engine."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fake_data import (
    FAKE_CLUSTER_NAME,
    FAKE_DEVICE_ID,
    FAKE_IRRIGATOR_NAME,
    FAKE_PLANT_SPECIES,
    FAKE_SENSOR_ID,
    FAKE_SENSOR_NAME,
)
from tuya_irrigation.db import IrrigationDB
from tuya_irrigation.learning import IrrigationLearner


class TestIrrigationLearner(unittest.TestCase):
    """Test learning engine with synthetic irrigation+sensor data."""

    def setUp(self):
        """Create temp DB with cluster, irrigator, sensor, and plant."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.db = IrrigationDB(Path(self.temp_db.name))
        self.learner = IrrigationLearner(self.db)

        self.cluster_id = self.db.add_cluster(FAKE_CLUSTER_NAME)
        self.plant_id = self.db.add_plant(
            cluster_id=self.cluster_id,
            species=FAKE_PLANT_SPECIES,
            water_needs="medium",
        )
        self.irrigator_id = self.db.add_irrigator(
            cluster_id=self.cluster_id,
            tuya_device_id=FAKE_DEVICE_ID,
            name=FAKE_IRRIGATOR_NAME,
            irrigator_type="tuya_cloud",
            config={},
        )
        self.sensor_id = self.db.add_sensor(
            cluster_id=self.cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name=FAKE_SENSOR_NAME,
            sensor_type="soil_moisture",
            config={},
            plant_id=self.plant_id,
        )

    def tearDown(self):
        self.db.close()
        Path(self.temp_db.name).unlink()

    def _simulate_irrigation_cycle(
        self, base_time: int, pre_moisture: float, post_moisture: float, duration: int = 2
    ):
        """Simulate an irrigation event with pre and post sensor readings."""
        # Pre-reading: 15min before irrigation
        self.db.add_sensor_reading(
            sensor_id=self.sensor_id,
            timestamp=base_time - 900,
            soil_moisture=pre_moisture,
            temperature=22.0,
        )
        # Irrigation event
        event_id = self.db.add_irrigation_event(
            irrigator_id=self.irrigator_id,
            action="start",
            triggered_by="auto",
            duration_minutes=duration,
            timestamp=base_time,
        )
        # Post-reading: 30min after irrigation
        self.db.add_sensor_reading(
            sensor_id=self.sensor_id,
            timestamp=base_time + 1800,
            soil_moisture=post_moisture,
            temperature=22.0,
        )
        return event_id

    def test_analyze_response_positive_delta(self):
        """Irrigation response shows positive moisture delta."""
        now = int(time.time())
        self._simulate_irrigation_cycle(now, pre_moisture=30.0, post_moisture=50.0)

        event = self.db.get_recent_events(self.irrigator_id, hours=1)[0]
        responses = self.learner.analyze_irrigation_response(event)

        self.assertEqual(len(responses), 1)
        r = responses[0]
        self.assertEqual(r.sensor_id, self.sensor_id)
        self.assertAlmostEqual(r.pre_moisture, 30.0)
        self.assertAlmostEqual(r.post_moisture, 50.0)
        self.assertAlmostEqual(r.delta, 20.0)
        self.assertAlmostEqual(r.delta_per_minute, 10.0)  # 20% / 2min

    def test_analyze_response_no_change(self):
        """No moisture change detected (possible blocked drip)."""
        now = int(time.time())
        self._simulate_irrigation_cycle(now, pre_moisture=30.0, post_moisture=31.0)

        event = self.db.get_recent_events(self.irrigator_id, hours=1)[0]
        responses = self.learner.analyze_irrigation_response(event)

        self.assertEqual(len(responses), 1)
        self.assertAlmostEqual(responses[0].delta, 1.0)

    def test_plant_profile_builds_from_multiple_cycles(self):
        """Plant profile builds correctly from 3+ irrigation cycles."""
        now = int(time.time())
        # Simulate 4 irrigation cycles over 4 days
        for i in range(4):
            cycle_time = now - (i * 86400)  # 1 day apart
            self._simulate_irrigation_cycle(
                cycle_time,
                pre_moisture=25.0 + i,
                post_moisture=45.0 + i,
                duration=2,
            )

        sensor = self.db.get_sensors_in_cluster(self.cluster_id)[0]
        profile = self.learner.get_plant_profile(sensor, days=30)

        self.assertIsNotNone(profile)
        self.assertEqual(profile.response_count, 4)
        self.assertGreater(profile.avg_absorption_per_minute, 0)
        self.assertGreater(profile.efficiency_score, 0.5)  # All positive deltas

    def test_plant_profile_insufficient_data(self):
        """Plant profile returns None with no irrigation history."""
        sensor = self.db.get_sensors_in_cluster(self.cluster_id)[0]
        profile = self.learner.get_plant_profile(sensor, days=30)
        self.assertIsNone(profile)

    def test_detect_no_issues_with_insufficient_data(self):
        """No alerts when not enough data."""
        alerts = self.learner.detect_issues(self.cluster_id)
        self.assertEqual(len(alerts), 0)

    def test_drainage_rate_computation(self):
        """Drainage rate computed from declining moisture readings."""
        now = int(time.time())
        # Simulate natural drying: -4% per hour for 5 hours
        for h in range(6):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id,
                timestamp=now - (5 - h) * 3600,
                soil_moisture=60.0 - (h * 4),
            )

        sensor = self.db.get_sensors_in_cluster(self.cluster_id)[0]
        drainage = self.learner._compute_drainage_rate(sensor, days=1)

        self.assertLess(drainage, 0)  # Negative = losing moisture
        self.assertAlmostEqual(drainage, -4.0, places=0)

    def test_generate_report_with_data(self):
        """Report generates text output with profiles."""
        now = int(time.time())
        for i in range(3):
            self._simulate_irrigation_cycle(
                now - (i * 86400),
                pre_moisture=30.0,
                post_moisture=50.0,
            )

        report = self.learner.generate_report(self.cluster_id)

        self.assertIn("Irrigation Learning Report", report)
        self.assertIn(FAKE_SENSOR_NAME, report)
        self.assertIn("Absorption", report)

    def test_generate_report_no_data(self):
        """Report shows insufficient data message."""
        report = self.learner.generate_report(self.cluster_id)
        self.assertIn("insufficient data", report)

    def test_generate_report_empty_cluster(self):
        """Report handles cluster with no sensors."""
        empty_cluster = self.db.add_cluster("Empty")
        report = self.learner.generate_report(empty_cluster)
        self.assertIn("No sensors", report)


if __name__ == "__main__":
    unittest.main()
