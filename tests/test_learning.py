"""Test suite for irrigation learning engine."""

import time

import pytest

from fake_data import (
    FAKE_CLUSTER_NAME,
    FAKE_DEVICE_ID,
    FAKE_DEVICE_ID_2,
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

    def test_response_peak_ignores_a_spike_reading(self):
        """The post value is the window's *maximum* — exactly what a spike wins.

        A blocked drip (30% → 31%) with one 99% glitch in the after-window must
        not be learned as a 69-point absorption event.
        """
        now = int(time.time())
        self.db.add_sensor_reading(sensor_id=self.sensor_id, timestamp=now - 1500, soil_moisture=30.0)
        self.db.add_sensor_reading(sensor_id=self.sensor_id, timestamp=now - 900, soil_moisture=30.0)
        self.db.add_irrigation_event(
            irrigator_id=self.irrigator_id,
            action="start",
            triggered_by="auto",
            duration_minutes=2,
            timestamp=now,
        )
        for offset, moisture in ((900, 31.0), (1800, 99.0), (2700, 30.0), (3600, 31.0)):
            self.db.add_sensor_reading(sensor_id=self.sensor_id, timestamp=now + offset, soil_moisture=moisture)

        event = self.db.get_recent_events(self.irrigator_id, hours=1)[0]
        responses = self.learner.analyze_irrigation_response(event)

        assert len(responses) == 1
        assert responses[0].post_moisture == pytest.approx(31.0)
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


class TestIssueHeuristics:
    """Behavior tests for the advisory issue-detection heuristics in issues.py.

    Each test drives ``detect_issues`` (or ``detect_conflicts`` directly for the
    multi-sensor heuristics that live inside it) with synthetic readings crafted
    to sit on the firing / non-firing side of each constant threshold:

    - LEARNING_RAPID_DRAINAGE_THRESHOLD = -5 %/hr
    - LEARNING_OVER_WATER_THRESHOLD     = 85 % moisture
    - LIGHT_BRIGHT                      = 800 lux (× seasonal factor)
    - low_light fires at avg_lux < effective_threshold * 0.5
    - low_env_humidity fires at avg < ideal_humidity_min - 15
    """

    @pytest.fixture(autouse=True)
    def setup(self, tmp_db):
        self.db = tmp_db
        self.learner = IrrigationLearner(self.db, get_plant_database())

        self.cluster_id = self.db.add_cluster(FAKE_CLUSTER_NAME)
        self.plant_id = self.db.add_plant(
            cluster_id=self.cluster_id,
            species=FAKE_PLANT_SPECIES,  # Monstera deliciosa: target 45-60, hum_min 60, lux_min 150
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

    # ── helpers ─────────────────────────────────────────────────────────────

    def _build_profile(self, sensor_id: int, irrigator_id: int, base_time: int, cycles: int = 5):
        """Create ``cycles`` healthy irrigation cycles so a PlantProfile builds.

        Each cycle is a +20% absorption event, spaced one day apart, which yields
        avg_absorption_per_minute ~ 10%/min and an efficiency_score of 1.0 — well
        above LEARNING_MIN_EVENTS / LEARNING_MIN_EFFICIENCY so the profile is used
        by the per-sensor heuristics.

        ``base_time`` should sit at least a week in the past for tests that also
        seed recent (≤6h / ≤48h) readings, so these profile readings don't leak
        into the moisture/drainage windows the heuristics scan.
        """
        for i in range(cycles):
            ts = base_time - (i * 86400)
            self.db.add_sensor_reading(sensor_id=sensor_id, timestamp=ts - 900, soil_moisture=30.0)
            self.db.add_irrigation_event(
                irrigator_id=irrigator_id,
                action="start",
                triggered_by="auto",
                duration_minutes=2,
                timestamp=ts,
            )
            self.db.add_sensor_reading(sensor_id=sensor_id, timestamp=ts + 1800, soil_moisture=50.0)

    def _add_second_sensor(self):
        """Add a second plant+sensor to the same cluster (shared single irrigator)."""
        plant2 = self.db.add_plant(
            cluster_id=self.cluster_id,
            species=FAKE_PLANT_SPECIES,
            water_needs="medium",
        )
        sensor2 = self.db.add_sensor(
            cluster_id=self.cluster_id,
            tuya_device_id=FAKE_DEVICE_ID_2,
            name="Test Sensor 2",
            sensor_type="soil_moisture",
            config={},
            plant_id=plant2,
        )
        return plant2, sensor2

    def _seed_recent_moisture(self, both: tuple[int, int], moisture: float, now: int):
        """Give both sensors a single recent in-range moisture reading.

        ``detect_conflicts`` returns early when fewer than two sensors have a
        recent (≤6h) moisture reading, which would skip the low_light /
        low_env_humidity loops that live after that guard. Seeding one in-range
        reading per sensor reaches those loops without creating a dry/wet conflict.
        """
        for sid in both:
            self.db.add_sensor_reading(sensor_id=sid, timestamp=now - 300, soil_moisture=moisture)

    def _care_map(self):
        """Build the plant_care map exactly as detect_issues does, for direct calls."""
        plants = self.db.get_plants_in_cluster(self.cluster_id)
        return {p.id: self.learner.plant_db.get_care_data(species=p.species, category=p.category) for p in plants}

    # ── light_accelerated_drainage vs rapid_drainage ────────────────────────

    def test_light_accelerated_drainage_alert(self, monkeypatch):
        """Rapid drainage under bright daytime light fires light_accelerated_drainage."""
        # Pin the seasonal factor so the 800-lux bright threshold is deterministic.
        monkeypatch.setattr("greenhouse_core.learning.issues.seasonal_light_factor", lambda *a, **k: 1.0)

        now = int(time.time())
        self._build_profile(self.sensor_id, self.irrigator_id, now - 7 * 86400)
        # Steeply declining moisture (< -5 %/hr) plus bright light (> 800 lux).
        # Older readings (larger h) are wetter so moisture falls ~7 %/hr in time.
        for h in range(10):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id,
                timestamp=now - h * 3600,
                soil_moisture=5.0 + (h * 7),  # chronologically ~-7 %/hr
                light=20000,  # well above 800 and above NIGHT_LUX_THRESHOLD
            )

        alerts = self.learner.detect_issues(self.cluster_id)
        types = [a.alert_type for a in alerts]
        assert "light_accelerated_drainage" in types
        assert "rapid_drainage" not in types
        alert = next(a for a in alerts if a.alert_type == "light_accelerated_drainage")
        assert alert.severity == "warning"
        assert alert.data["avg_lux"] > 800

    def test_rapid_drainage_without_bright_light(self, monkeypatch):
        """Rapid drainage in dim light fires plain rapid_drainage, not the light variant."""
        monkeypatch.setattr("greenhouse_core.learning.issues.seasonal_light_factor", lambda *a, **k: 1.0)

        now = int(time.time())
        self._build_profile(self.sensor_id, self.irrigator_id, now - 7 * 86400)
        for h in range(10):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id,
                timestamp=now - h * 3600,
                soil_moisture=5.0 + (h * 7),  # chronologically ~-7 %/hr
                light=100,  # daytime but below the 800-lux bright threshold
            )

        alerts = self.learner.detect_issues(self.cluster_id)
        types = [a.alert_type for a in alerts]
        assert "rapid_drainage" in types
        assert "light_accelerated_drainage" not in types

    def test_no_drainage_alert_when_retention_healthy(self):
        """Stable moisture (drainage above -5 %/hr) fires no drainage alert."""
        now = int(time.time())
        self._build_profile(self.sensor_id, self.irrigator_id, now - 7 * 86400)
        # Gentle decline ~-1 %/hr (older wetter), well above the -5 threshold.
        for h in range(10):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id,
                timestamp=now - h * 3600,
                soil_moisture=45.0 + h,  # chronologically ~-1 %/hr
                light=20000,
            )

        alerts = self.learner.detect_issues(self.cluster_id)
        types = [a.alert_type for a in alerts]
        assert "rapid_drainage" not in types
        assert "light_accelerated_drainage" not in types

    # ── chronic_underwatering ───────────────────────────────────────────────

    def test_chronic_underwatering_alert(self):
        """Soil that never reaches target_min over 7d fires chronic_underwatering."""
        now = int(time.time())
        # >=5 cycles required (response_count >= 5). Keep post-moisture below the
        # 45% Monstera target_min so the 7d peak stays under target.
        for i in range(6):
            ts = now - (i * 86400)
            self.db.add_sensor_reading(sensor_id=self.sensor_id, timestamp=ts - 900, soil_moisture=20.0)
            self.db.add_irrigation_event(
                irrigator_id=self.irrigator_id,
                action="start",
                triggered_by="auto",
                duration_minutes=2,
                timestamp=ts,
            )
            self.db.add_sensor_reading(sensor_id=self.sensor_id, timestamp=ts + 1800, soil_moisture=35.0)

        alerts = self.learner.detect_issues(self.cluster_id)
        types = [a.alert_type for a in alerts]
        assert "chronic_underwatering" in types
        alert = next(a for a in alerts if a.alert_type == "chronic_underwatering")
        assert alert.data["max_recent"] < alert.data["target_min"]

    def test_no_chronic_underwatering_when_target_reached(self):
        """Soil that reaches target_min does not fire chronic_underwatering."""
        now = int(time.time())
        for i in range(6):
            ts = now - (i * 86400)
            self.db.add_sensor_reading(sensor_id=self.sensor_id, timestamp=ts - 900, soil_moisture=40.0)
            self.db.add_irrigation_event(
                irrigator_id=self.irrigator_id,
                action="start",
                triggered_by="auto",
                duration_minutes=2,
                timestamp=ts,
            )
            self.db.add_sensor_reading(sensor_id=self.sensor_id, timestamp=ts + 1800, soil_moisture=58.0)

        alerts = self.learner.detect_issues(self.cluster_id)
        assert "chronic_underwatering" not in [a.alert_type for a in alerts]

    # ── unresolvable_conflict ───────────────────────────────────────────────

    def test_unresolvable_conflict_alert(self):
        """Dry plant needing long irrigation that would over-saturate a wet plant."""
        now = int(time.time())
        _plant2, sensor2 = self._add_second_sensor()
        # Both sensors need profiles (avg_absorption_per_minute drives the projection).
        self._build_profile(self.sensor_id, self.irrigator_id, now - 7 * 86400)
        self._build_profile(sensor2, self.irrigator_id, now - 7 * 86400)

        # Sensor 1 dry (below target_min - 5 = 40), sensor 2 already wet (above
        # target_max = 60). Watering sensor 1 up to 60 takes many minutes; at
        # ~10%/min absorption sensor 2 would blow past LEARNING_OVER_WATER_THRESHOLD (85).
        self.db.add_sensor_reading(sensor_id=self.sensor_id, timestamp=now - 600, soil_moisture=20.0)
        self.db.add_sensor_reading(sensor_id=sensor2, timestamp=now - 600, soil_moisture=80.0)

        profiles = {
            self.sensor_id: self.learner.get_plant_profile(self.db.get_sensor(self.sensor_id)),
            sensor2: self.learner.get_plant_profile(self.db.get_sensor(sensor2)),
        }
        from greenhouse_core.learning.issues import detect_conflicts

        alerts = detect_conflicts(self.db, self.learner.plant_db, self.cluster_id, profiles, self._care_map())
        types = [a.alert_type for a in alerts]
        assert "unresolvable_conflict" in types
        alert = next(a for a in alerts if a.alert_type == "unresolvable_conflict")
        assert alert.severity == "critical"
        assert alert.data["projected_wet"] > 85

    def test_conflict_keys_on_the_latest_readings_not_the_oldest(self):
        """The dry/wet snapshot must average the *newest* samples in the window.

        ``get_recent_readings`` returns newest-first, so slicing the tail of
        that list averaged the OLDEST readings in the 6h window — a plant that
        had just been watered still counted as dry (issue #103 family: values
        attributed to the wrong point in time).
        """
        now = int(time.time())
        _plant2, sensor2 = self._add_second_sensor()
        self._build_profile(self.sensor_id, self.irrigator_id, now - 7 * 86400)
        self._build_profile(sensor2, self.irrigator_id, now - 7 * 86400)

        # Sensor 1: dry hours ago, comfortably in range now. Sensor 2 wet
        # throughout. Judged on the latest samples there is no dry plant, so no
        # conflict; judged on the oldest ones sensor 1 still looks parched.
        for i, moisture in enumerate([20.0, 21.0, 22.0]):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id, timestamp=now - 5 * 3600 + i * 60, soil_moisture=moisture
            )
        for i, moisture in enumerate([52.0, 53.0, 54.0]):
            self.db.add_sensor_reading(sensor_id=self.sensor_id, timestamp=now - 600 + i * 60, soil_moisture=moisture)
        self.db.add_sensor_reading(sensor_id=sensor2, timestamp=now - 600, soil_moisture=80.0)

        profiles = {
            self.sensor_id: self.learner.get_plant_profile(self.db.get_sensor(self.sensor_id)),
            sensor2: self.learner.get_plant_profile(self.db.get_sensor(sensor2)),
        }
        from greenhouse_core.learning.issues import detect_conflicts

        alerts = detect_conflicts(self.db, self.learner.plant_db, self.cluster_id, profiles, self._care_map())

        assert "unresolvable_conflict" not in [a.alert_type for a in alerts]

    def test_no_conflict_when_both_plants_in_range(self):
        """No conflict when neither plant is dry nor over-saturated."""
        now = int(time.time())
        _plant2, sensor2 = self._add_second_sensor()
        self._build_profile(self.sensor_id, self.irrigator_id, now - 7 * 86400)
        self._build_profile(sensor2, self.irrigator_id, now - 7 * 86400)

        # Both comfortably inside the 45-60 band → no dry, no wet sensors.
        self.db.add_sensor_reading(sensor_id=self.sensor_id, timestamp=now - 600, soil_moisture=50.0)
        self.db.add_sensor_reading(sensor_id=sensor2, timestamp=now - 600, soil_moisture=52.0)

        profiles = {
            self.sensor_id: self.learner.get_plant_profile(self.db.get_sensor(self.sensor_id)),
            sensor2: self.learner.get_plant_profile(self.db.get_sensor(sensor2)),
        }
        from greenhouse_core.learning.issues import detect_conflicts

        alerts = detect_conflicts(self.db, self.learner.plant_db, self.cluster_id, profiles, self._care_map())
        assert "unresolvable_conflict" not in [a.alert_type for a in alerts]

    # ── low_light ───────────────────────────────────────────────────────────

    def test_low_light_alert(self, monkeypatch):
        """Sustained daytime light below half the seasonal minimum fires low_light."""
        # Pin threshold: effective(150) = 150, fires below 75 lux.
        monkeypatch.setattr(
            "greenhouse_core.learning.issues.effective_light_threshold", lambda base, *a, **k: float(base)
        )
        now = int(time.time())
        _plant2, sensor2 = self._add_second_sensor()  # need >=2 profiles to reach detect_conflicts
        self._build_profile(self.sensor_id, self.irrigator_id, now - 7 * 86400)
        self._build_profile(sensor2, self.irrigator_id, now - 7 * 86400)

        # The low_light loop sits after detect_conflicts' `len(sensor_moisture) < 2`
        # early return, so both sensors need a recent (≤6h) in-range moisture
        # reading for it to be reached. Keep both inside the 45-60 band → no conflict.
        self._seed_recent_moisture(both=(self.sensor_id, sensor2), moisture=50.0, now=now)

        # >=5 daytime readings (light > 15) averaging well under 75 lux.
        for h in range(8):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id,
                timestamp=now - h * 3600 - 60,
                light=40,
            )

        profiles = {
            self.sensor_id: self.learner.get_plant_profile(self.db.get_sensor(self.sensor_id)),
            sensor2: self.learner.get_plant_profile(self.db.get_sensor(sensor2)),
        }
        from greenhouse_core.learning.issues import detect_conflicts

        alerts = detect_conflicts(self.db, self.learner.plant_db, self.cluster_id, profiles, self._care_map())
        low_light = [a for a in alerts if a.alert_type == "low_light" and a.sensor_name == FAKE_SENSOR_NAME]
        assert low_light
        assert low_light[0].severity == "warning"
        assert low_light[0].data["avg_lux"] < 75

    def test_no_low_light_when_bright_enough(self, monkeypatch):
        """Adequate daytime light does not fire low_light."""
        monkeypatch.setattr(
            "greenhouse_core.learning.issues.effective_light_threshold", lambda base, *a, **k: float(base)
        )
        now = int(time.time())
        _plant2, sensor2 = self._add_second_sensor()
        self._build_profile(self.sensor_id, self.irrigator_id, now - 7 * 86400)
        self._build_profile(sensor2, self.irrigator_id, now - 7 * 86400)
        self._seed_recent_moisture(both=(self.sensor_id, sensor2), moisture=50.0, now=now)

        for h in range(8):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id,
                timestamp=now - h * 3600 - 60,
                light=5000,  # far above the 75-lux floor
            )

        profiles = {
            self.sensor_id: self.learner.get_plant_profile(self.db.get_sensor(self.sensor_id)),
            sensor2: self.learner.get_plant_profile(self.db.get_sensor(sensor2)),
        }
        from greenhouse_core.learning.issues import detect_conflicts

        alerts = detect_conflicts(self.db, self.learner.plant_db, self.cluster_id, profiles, self._care_map())
        sensor1_low_light = [a for a in alerts if a.alert_type == "low_light" and a.sensor_name == FAKE_SENSOR_NAME]
        assert not sensor1_low_light

    # ── low_env_humidity ────────────────────────────────────────────────────

    def test_low_env_humidity_alert(self):
        """Ambient humidity far below ideal (ideal_min - 15) fires low_env_humidity."""
        now = int(time.time())
        _plant2, sensor2 = self._add_second_sensor()
        self._build_profile(self.sensor_id, self.irrigator_id, now - 7 * 86400)
        self._build_profile(sensor2, self.irrigator_id, now - 7 * 86400)
        self._seed_recent_moisture(both=(self.sensor_id, sensor2), moisture=50.0, now=now)

        # Monstera ideal_humidity_min = 60 → fires below 45. Use ~30%.
        for h in range(8):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id,
                timestamp=now - h * 3600 - 60,
                env_humidity=30.0,
            )

        profiles = {
            self.sensor_id: self.learner.get_plant_profile(self.db.get_sensor(self.sensor_id)),
            sensor2: self.learner.get_plant_profile(self.db.get_sensor(sensor2)),
        }
        from greenhouse_core.learning.issues import detect_conflicts

        alerts = detect_conflicts(self.db, self.learner.plant_db, self.cluster_id, profiles, self._care_map())
        hum = [a for a in alerts if a.alert_type == "low_env_humidity" and a.sensor_name == FAKE_SENSOR_NAME]
        assert hum
        assert hum[0].severity == "warning"
        assert hum[0].data["avg_env_humidity"] < hum[0].data["ideal_min"] - 15

    def test_no_low_env_humidity_when_humid_enough(self):
        """Humidity within ~15% of ideal does not fire low_env_humidity."""
        now = int(time.time())
        _plant2, sensor2 = self._add_second_sensor()
        self._build_profile(self.sensor_id, self.irrigator_id, now - 7 * 86400)
        self._build_profile(sensor2, self.irrigator_id, now - 7 * 86400)
        self._seed_recent_moisture(both=(self.sensor_id, sensor2), moisture=50.0, now=now)

        # 55% is within ideal_min(60) - 15 = 45, so no alert.
        for h in range(8):
            self.db.add_sensor_reading(
                sensor_id=self.sensor_id,
                timestamp=now - h * 3600 - 60,
                env_humidity=55.0,
            )

        profiles = {
            self.sensor_id: self.learner.get_plant_profile(self.db.get_sensor(self.sensor_id)),
            sensor2: self.learner.get_plant_profile(self.db.get_sensor(sensor2)),
        }
        from greenhouse_core.learning.issues import detect_conflicts

        alerts = detect_conflicts(self.db, self.learner.plant_db, self.cluster_id, profiles, self._care_map())
        sensor1_hum = [a for a in alerts if a.alert_type == "low_env_humidity" and a.sensor_name == FAKE_SENSOR_NAME]
        assert not sensor1_hum
