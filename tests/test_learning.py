"""Test suite for irrigation learning engine."""

import time

import pytest

from fake_data import (
    FAKE_CLUSTER_NAME,
    FAKE_DEVICE_ID,
    FAKE_IRRIGATOR_NAME,
    FAKE_PLANT_SPECIES,
    FAKE_SENSOR_ID,
    FAKE_SENSOR_NAME,
)
from tuya_irrigation_core.learning import IrrigationLearner


class TestIrrigationLearner:
    """Test learning engine with synthetic irrigation+sensor data."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_db):
        self.db = tmp_db
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

    def _simulate_irrigation_cycle(self, base_time: int, pre_moisture: float, post_moisture: float, duration: int = 2):
        """Simulate an irrigation event with pre and post sensor readings."""
        self.db.add_sensor_reading(
            sensor_id=self.sensor_id,
            timestamp=base_time - 900,
            soil_moisture=pre_moisture,
            temperature=22.0,
        )
        event_id = self.db.add_irrigation_event(
            irrigator_id=self.irrigator_id,
            action="start",
            triggered_by="auto",
            duration_minutes=duration,
            timestamp=base_time,
        )
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

        assert len(responses) == 1
        r = responses[0]
        assert r.sensor_id == self.sensor_id
        assert r.pre_moisture == pytest.approx(30.0)
        assert r.post_moisture == pytest.approx(50.0)
        assert r.delta == pytest.approx(20.0)
        assert r.delta_per_minute == pytest.approx(10.0)

    def test_analyze_response_no_change(self):
        """No moisture change detected (possible blocked drip)."""
        now = int(time.time())
        self._simulate_irrigation_cycle(now, pre_moisture=30.0, post_moisture=31.0)

        event = self.db.get_recent_events(self.irrigator_id, hours=1)[0]
        responses = self.learner.analyze_irrigation_response(event)

        assert len(responses) == 1
        assert responses[0].delta == pytest.approx(1.0)

    def test_plant_profile_builds_from_multiple_cycles(self):
        """Plant profile builds correctly from 3+ irrigation cycles."""
        now = int(time.time())
        for i in range(4):
            cycle_time = now - (i * 86400)
            self._simulate_irrigation_cycle(
                cycle_time,
                pre_moisture=25.0 + i,
                post_moisture=45.0 + i,
                duration=2,
            )

        sensor = self.db.get_sensors_in_cluster(self.cluster_id)[0]
        profile = self.learner.get_plant_profile(sensor, days=30)

        assert profile is not None
        assert profile.response_count == 4
        assert profile.avg_absorption_per_minute > 0
        assert profile.efficiency_score > 0.5

    def test_plant_profile_insufficient_data(self):
        """Plant profile returns None with no irrigation history."""
        sensor = self.db.get_sensors_in_cluster(self.cluster_id)[0]
        profile = self.learner.get_plant_profile(sensor, days=30)
        assert profile is None

    def test_detect_no_issues_with_insufficient_data(self):
        """No alerts when not enough data."""
        alerts = self.learner.detect_issues(self.cluster_id)
        assert len(alerts) == 0

    def test_drainage_rate_computation(self):
        """Drainage rate computed from declining moisture readings."""
        now = int(time.time())
        for h in range(6):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id,
                timestamp=now - (5 - h) * 3600,
                soil_moisture=60.0 - (h * 4),
            )

        sensor = self.db.get_sensors_in_cluster(self.cluster_id)[0]
        drainage = self.learner._compute_drainage_rate(sensor, days=1)

        assert drainage < 0
        assert drainage == pytest.approx(-4.0, abs=0.5)

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

        assert "Irrigation Learning Report" in report
        assert FAKE_SENSOR_NAME in report
        assert "Absorption" in report

    def test_generate_report_no_data(self):
        """Report shows insufficient data message."""
        report = self.learner.generate_report(self.cluster_id)
        assert "insufficient data" in report

    def test_generate_report_empty_cluster(self):
        """Report handles cluster with no sensors."""
        empty_cluster = self.db.add_cluster("Empty")
        report = self.learner.generate_report(empty_cluster)
        assert "No sensors" in report
