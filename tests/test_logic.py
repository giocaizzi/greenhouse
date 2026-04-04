"""Test suite for irrigation system - Smart logic."""

import time

import pytest

from fake_data import FAKE_CLUSTER_NAME, FAKE_PLANT_SPECIES, FAKE_SENSOR_ID
from tuya_irrigation.logic import IrrigationLogic


class TestIrrigationLogic:
    """Test smart irrigation decision logic."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_db):
        self.db = tmp_db
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

    def _add_soil_sensor(self, moisture: float, device_id: str = FAKE_SENSOR_ID) -> int:
        """Helper: add a soil sensor with one reading."""
        sensor_id = self.db.add_sensor(
            cluster_id=self.cluster_id,
            tuya_device_id=device_id,
            name="Fake Soil Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        self.db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=moisture)
        return sensor_id

    def test_temperature_fallback_cold(self):
        """Cold temperature suggests longer interval."""
        decision = self.logic.decide_for_cluster(self.cluster_id, current_temp=15.0)

        assert decision is not None
        assert decision["action"] == "irrigate"
        assert decision["interval_hours"] > 18
        assert "temperature-based" in decision["reason"]

    def test_temperature_fallback_hot(self):
        """Hot temperature suggests shorter interval."""
        decision = self.logic.decide_for_cluster(self.cluster_id, current_temp=30.0)

        assert decision is not None
        assert decision["action"] == "irrigate"
        assert decision["interval_hours"] < 8
        assert "temperature-based" in decision["reason"]

    def test_temperature_fallback_moderate(self):
        """Moderate temperature suggests medium interval."""
        decision = self.logic.decide_for_cluster(self.cluster_id, current_temp=22.0)

        assert decision is not None
        assert decision["action"] == "irrigate"
        assert decision["interval_hours"] > 10
        assert decision["interval_hours"] < 14

    def test_soil_moisture_dry(self):
        """Dry soil triggers irrigation."""
        self._add_soil_sensor(moisture=25.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision["action"] == "irrigate"
        assert decision["confidence"] > 0.8
        reason_lower = decision["reason"].lower()
        assert "soil" in reason_lower or "stress" in reason_lower

    def test_soil_moisture_adequate(self):
        """Adequate soil moisture skips irrigation."""
        self._add_soil_sensor(moisture=55.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision["action"] == "skip"
        assert "adequate" in decision["reason"].lower()

    def test_confidence_without_sensors(self):
        """Temperature-based decision has lower confidence."""
        decision = self.logic.decide_for_cluster(self.cluster_id, current_temp=22.0)
        assert decision["confidence"] < 0.7

    def test_confidence_with_sensors(self):
        """Sensor-based decision has higher confidence."""
        self._add_soil_sensor(moisture=30.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision["confidence"] > 0.7

    def test_high_water_needs_adjustment(self):
        """High water needs plants get more frequent irrigation."""
        cluster_id = self.db.add_cluster("High Water Cluster")
        self.db.add_plant(
            cluster_id=cluster_id,
            species="Nephrolepis exaltata",
            category="fern",
            water_needs="high",
        )
        decision = self.logic.decide_for_cluster(cluster_id, current_temp=22.0)
        assert decision["interval_hours"] <= 8

    def test_low_water_needs_adjustment(self):
        """Low water needs plants get less frequent irrigation."""
        cluster_id = self.db.add_cluster("Succulent Cluster")
        self.db.add_plant(
            cluster_id=cluster_id,
            species="Echeveria elegans",
            category="succulent",
            water_needs="low",
        )
        decision = self.logic.decide_for_cluster(cluster_id, current_temp=22.0)
        assert decision["interval_hours"] > 14

    def test_no_plants_returns_skip(self):
        """Cluster with no plants returns skip action."""
        empty_cluster = self.db.add_cluster("Empty Cluster")
        decision = self.logic.decide_for_cluster(empty_cluster)
        assert decision["action"] == "skip"
        assert "no plants" in decision["reason"].lower()

    def test_nonexistent_cluster(self):
        """Nonexistent cluster returns None."""
        decision = self.logic.decide_for_cluster(99999)
        assert decision is None

    def test_multi_sensor_driest_triggers(self):
        """With multiple sensors, driest plant triggers irrigation."""
        cluster_id = self.db.add_cluster("Multi Sensor Cluster")
        self.db.add_plant(
            cluster_id=cluster_id,
            species=FAKE_PLANT_SPECIES,
            water_needs="medium",
        )

        s_a = self.db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id="fake_sensor_a",
            name="Dry Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        self.db.add_sensor_reading(sensor_id=s_a, soil_moisture=20.0)

        s_b = self.db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id="fake_sensor_b",
            name="OK Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        self.db.add_sensor_reading(sensor_id=s_b, soil_moisture=50.0)

        decision = self.logic.decide_for_cluster(cluster_id)
        assert decision["action"] == "irrigate"
        assert "driest" in decision["reason"].lower()

    def test_multi_sensor_conflict_short_burst(self):
        """Conflicting sensors (one dry, one wet) trigger short burst."""
        cluster_id = self.db.add_cluster("Conflict Cluster")
        self.db.add_plant(
            cluster_id=cluster_id,
            species=FAKE_PLANT_SPECIES,
            water_needs="medium",
        )

        s_a = self.db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id="fake_sensor_dry",
            name="Dry Plant",
            sensor_type="soil_moisture",
            config={},
        )
        self.db.add_sensor_reading(sensor_id=s_a, soil_moisture=15.0)

        s_b = self.db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id="fake_sensor_wet",
            name="Wet Plant",
            sensor_type="soil_moisture",
            config={},
        )
        self.db.add_sensor_reading(sensor_id=s_b, soil_moisture=60.0)

        decision = self.logic.decide_for_cluster(cluster_id)
        assert decision["action"] == "irrigate"
        assert decision["duration_minutes"] == 1  # Short burst
        assert "conflict" in decision["reason"].lower()
        assert decision["confidence"] < 0.7

    def test_all_sensors_adequate_skips(self):
        """All sensors showing adequate moisture skips irrigation."""
        cluster_id = self.db.add_cluster("Adequate Cluster")
        self.db.add_plant(
            cluster_id=cluster_id,
            species=FAKE_PLANT_SPECIES,
            water_needs="medium",
        )

        for i, name in enumerate(["Sensor A", "Sensor B", "Sensor C"]):
            sid = self.db.add_sensor(
                cluster_id=cluster_id,
                tuya_device_id=f"fake_sensor_{i}",
                name=name,
                sensor_type="soil_moisture",
                config={},
            )
            self.db.add_sensor_reading(sensor_id=sid, soil_moisture=55.0 + i)

        decision = self.logic.decide_for_cluster(cluster_id)
        assert decision["action"] == "skip"
        assert "adequate" in decision["reason"].lower()

    def test_cooldown_blocks_irrigation(self):
        """Recent irrigation triggers cooldown — skips even if dry."""
        cluster_id = self.db.add_cluster("Cooldown Cluster")
        self.db.add_plant(cluster_id=cluster_id, species=FAKE_PLANT_SPECIES, water_needs="medium")
        irrigator_id = self.db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id="fake_irr_cd",
            name="Irrigator",
            irrigator_type="tuya_cloud",
            config={},
        )
        # Irrigation happened 1 hour ago
        self.db.add_irrigation_event(
            irrigator_id=irrigator_id,
            action="start",
            triggered_by="auto",
            duration_minutes=2,
            timestamp=int(time.time()) - 3600,
        )
        sensor_id = self.db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id="fake_sensor_cd",
            name="Dry Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        self.db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=20.0)

        logic = IrrigationLogic(self.db)
        decision = logic.decide_for_cluster(cluster_id)
        assert decision["action"] == "skip"
        assert "cooldown" in decision["reason"].lower()

    def test_no_data_returns_low_confidence(self):
        """No sensors and no temp returns low confidence skip."""
        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision["action"] == "skip"
        assert decision["confidence"] <= 0.3
