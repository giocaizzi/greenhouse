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
from greenhouse_core.learning import IrrigationLearner
from greenhouse_core.plant_db import get_plant_database


class TestIrrigationLearner:
    """Test learning engine with synthetic irrigation+sensor data."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_db):
        self.db = tmp_db
        self.learner = IrrigationLearner(self.db, get_plant_database())

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
        from greenhouse_core.learning.profiling import compute_drainage_rate

        drainage = compute_drainage_rate(self.db, sensor, days=1)

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

    # ── Edge case: anomalous moisture ───────────────────────────────────────

    def test_analyze_response_very_high_moisture(self):
        """Response correctly handles extreme moisture spike (>95%)."""
        now = int(time.time())
        self._simulate_irrigation_cycle(now, pre_moisture=80.0, post_moisture=98.0)

        event = self.db.get_recent_events(self.irrigator_id, hours=1)[0]
        responses = self.learner.analyze_irrigation_response(event)

        assert len(responses) == 1
        assert responses[0].delta == pytest.approx(18.0)
        assert responses[0].post_moisture == pytest.approx(98.0)

    def test_analyze_response_negative_delta(self):
        """Moisture drop after irrigation (e.g., shallow sensor, water drains)."""
        now = int(time.time())
        self._simulate_irrigation_cycle(now, pre_moisture=45.0, post_moisture=40.0)

        event = self.db.get_recent_events(self.irrigator_id, hours=1)[0]
        responses = self.learner.analyze_irrigation_response(event)

        assert len(responses) == 1
        assert responses[0].delta == pytest.approx(-5.0)

    # ── Edge case: profile with minimal data ────────────────────────────────

    def test_plant_profile_with_mixed_responses(self):
        """Profile handles a mix of positive and negative deltas."""
        now = int(time.time())
        deltas = [(30, 50), (40, 38), (25, 55), (35, 36)]  # 2 positive, 2 negative
        for i, (pre, post) in enumerate(deltas):
            self._simulate_irrigation_cycle(
                now - (i * 86400),
                pre_moisture=pre,
                post_moisture=post,
            )

        sensor = self.db.get_sensors_in_cluster(self.cluster_id)[0]
        profile = self.learner.get_plant_profile(sensor, days=30)

        assert profile is not None
        assert profile.response_count == 4
        assert profile.efficiency_score == pytest.approx(0.5)  # 2 out of 4 had >2% increase
        assert profile.min_delta < 0

    def test_drainage_rate_no_decline(self):
        """Drainage rate returns 0 when moisture only rises."""
        now = int(time.time())
        for h in range(6):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id,
                timestamp=now - (5 - h) * 3600,
                soil_moisture=40.0 + (h * 3),  # Rising
            )

        sensor = self.db.get_sensors_in_cluster(self.cluster_id)[0]
        from greenhouse_core.learning.profiling import compute_drainage_rate

        drainage = compute_drainage_rate(self.db, sensor, days=1)
        assert drainage == 0.0

    # ── Issue detection edge cases ──────────────────────────────────────────

    def test_blocked_drip_alert(self):
        """Consistently near-zero delta triggers blocked_drip alert."""
        now = int(time.time())
        for i in range(5):
            self._simulate_irrigation_cycle(
                now - (i * 86400),
                pre_moisture=30.0,
                post_moisture=30.5,  # Barely any change
                duration=2,
            )

        alerts = self.learner.detect_issues(self.cluster_id)
        alert_types = [a.alert_type for a in alerts]
        assert "blocked_drip" in alert_types

    def test_no_blocked_drip_when_healthy(self):
        """No blocked_drip alert when irrigation responses are healthy."""
        now = int(time.time())
        for i in range(5):
            self._simulate_irrigation_cycle(
                now - (i * 86400),
                pre_moisture=35.0,
                post_moisture=55.0,  # Healthy +20% delta
                duration=2,
            )

        alerts = self.learner.detect_issues(self.cluster_id)
        alert_types = [a.alert_type for a in alerts]
        assert "blocked_drip" not in alert_types

    def test_rapid_drainage_alert(self):
        """Fast moisture loss triggers rapid_drainage alert."""
        now = int(time.time())
        # Build profile with enough events
        for i in range(5):
            self._simulate_irrigation_cycle(
                now - (i * 86400),
                pre_moisture=30.0,
                post_moisture=55.0,
                duration=2,
            )
        # Add rapidly declining readings (> 5%/hr)
        for h in range(10):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id,
                timestamp=now - h * 3600,
                soil_moisture=60.0 - (h * 8),  # -8%/hr, very rapid
            )

        alerts = self.learner.detect_issues(self.cluster_id)
        alert_types = [a.alert_type for a in alerts]
        assert "rapid_drainage" in alert_types or "light_accelerated_drainage" in alert_types

    def test_analyze_response_no_irrigator(self):
        """Analyze response returns empty list for deleted irrigator."""
        from greenhouse_core.models import IrrigationEvent

        fake_event = IrrigationEvent(
            id=999,
            irrigator_id=9999,  # Non-existent
            action="start",
            triggered_by="test",
            timestamp=int(time.time()),
            duration_minutes=2,
        )
        responses = self.learner.analyze_irrigation_response(fake_event)
        assert responses == []

    def test_detect_issues_empty_cluster(self):
        """No alerts for a cluster with no sensors."""
        empty_cluster = self.db.add_cluster("Empty Alert Cluster")
        alerts = self.learner.detect_issues(empty_cluster)
        assert alerts == []
