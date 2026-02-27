#!/usr/bin/env python3
"""Test suite for irrigation system - Smart logic."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fake_data import FAKE_CLUSTER_NAME, FAKE_PLANT_SPECIES, FAKE_SENSOR_ID
from tuya_irrigation.db import IrrigationDB
from tuya_irrigation.logic import IrrigationLogic


class TestIrrigationLogic(unittest.TestCase):
    """Test smart irrigation decision logic."""

    def setUp(self):
        """Create temporary database with fake test data."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.db = IrrigationDB(Path(self.temp_db.name))
        self.logic = IrrigationLogic(self.db)

        self.cluster_id = self.db.add_cluster(FAKE_CLUSTER_NAME)
        self.db.add_plant(
            cluster_id=self.cluster_id,
            species=FAKE_PLANT_SPECIES,
            water_needs="medium",
            ideal_temp_min=18.0,
            ideal_temp_max=27.0,
            ideal_humidity_min=60.0,
            ideal_humidity_max=80.0,
        )

    def tearDown(self):
        """Clean up temporary database."""
        self.db.close()
        Path(self.temp_db.name).unlink()

    def _add_soil_sensor(self, moisture: float) -> int:
        """Helper: add a soil sensor with one reading."""
        sensor_id = self.db.add_sensor(
            cluster_id=self.cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name="Fake Soil Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        self.db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=moisture)
        return sensor_id

    def test_temperature_fallback_cold(self):
        """Cold temperature suggests longer interval."""
        decision = self.logic.decide_for_cluster(self.cluster_id, current_temp=15.0)

        self.assertIsNotNone(decision)
        self.assertEqual(decision["action"], "irrigate")
        self.assertGreater(decision["interval_hours"], 18)
        self.assertIn("temperature-based", decision["reason"])

    def test_temperature_fallback_hot(self):
        """Hot temperature suggests shorter interval."""
        decision = self.logic.decide_for_cluster(self.cluster_id, current_temp=30.0)

        self.assertIsNotNone(decision)
        self.assertEqual(decision["action"], "irrigate")
        self.assertLess(decision["interval_hours"], 8)
        self.assertIn("temperature-based", decision["reason"])

    def test_temperature_fallback_moderate(self):
        """Moderate temperature suggests medium interval."""
        decision = self.logic.decide_for_cluster(self.cluster_id, current_temp=22.0)

        self.assertIsNotNone(decision)
        self.assertEqual(decision["action"], "irrigate")
        self.assertGreater(decision["interval_hours"], 10)
        self.assertLess(decision["interval_hours"], 14)

    def test_soil_moisture_dry(self):
        """Dry soil triggers irrigation."""
        self._add_soil_sensor(moisture=25.0)  # Very dry
        decision = self.logic.decide_for_cluster(self.cluster_id)

        self.assertEqual(decision["action"], "irrigate")
        self.assertGreater(decision["confidence"], 0.8)
        reason_lower = decision["reason"].lower()
        self.assertTrue("soil" in reason_lower or "stress" in reason_lower)

    def test_soil_moisture_adequate(self):
        """Adequate soil moisture skips irrigation."""
        self._add_soil_sensor(moisture=55.0)  # Healthy
        decision = self.logic.decide_for_cluster(self.cluster_id)

        self.assertEqual(decision["action"], "skip")
        self.assertIn("adequate", decision["reason"].lower())

    def test_confidence_without_sensors(self):
        """Temperature-based decision has lower confidence."""
        decision = self.logic.decide_for_cluster(self.cluster_id, current_temp=22.0)
        self.assertLess(decision["confidence"], 0.7)

    def test_confidence_with_sensors(self):
        """Sensor-based decision has higher confidence."""
        self._add_soil_sensor(moisture=30.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)
        self.assertGreater(decision["confidence"], 0.7)

    def test_high_water_needs_adjustment(self):
        """High water needs plants get more frequent irrigation."""
        cluster_id = self.db.add_cluster("High Water Cluster")
        self.db.add_plant(
            cluster_id=cluster_id,
            species="Nephrolepis exaltata",  # Boston fern — high water needs
            category="fern",
            water_needs="high",
        )
        decision = self.logic.decide_for_cluster(cluster_id, current_temp=22.0)
        self.assertLessEqual(decision["interval_hours"], 8)

    def test_low_water_needs_adjustment(self):
        """Low water needs plants get less frequent irrigation."""
        cluster_id = self.db.add_cluster("Succulent Cluster")
        self.db.add_plant(
            cluster_id=cluster_id,
            species="Echeveria elegans",  # Succulent — low water needs
            category="succulent",
            water_needs="low",
        )
        decision = self.logic.decide_for_cluster(cluster_id, current_temp=22.0)
        self.assertGreater(decision["interval_hours"], 14)

    def test_no_plants_returns_skip(self):
        """Cluster with no plants returns skip action."""
        empty_cluster = self.db.add_cluster("Empty Cluster")
        decision = self.logic.decide_for_cluster(empty_cluster)
        self.assertEqual(decision["action"], "skip")
        self.assertIn("no plants", decision["reason"].lower())

    def test_nonexistent_cluster(self):
        """Nonexistent cluster returns None."""
        decision = self.logic.decide_for_cluster(99999)
        self.assertIsNone(decision)


if __name__ == "__main__":
    unittest.main()
