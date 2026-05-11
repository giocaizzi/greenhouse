"""Test suite for irrigation system - Smart logic."""

import time

import pytest

from fake_data import FAKE_CLUSTER_NAME, FAKE_PLANT_SPECIES, FAKE_SENSOR_ID
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.plant_db import get_plant_database


class TestIrrigationLogic:
    """Test smart irrigation decision logic."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_db):
        self.db = tmp_db
        self.logic = IrrigationLogic(self.db, get_plant_database())
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
        assert decision.action.value == "irrigate"
        assert decision.interval_hours > 18
        assert "temperature-based" in decision.reason_text

    def test_temperature_fallback_hot(self):
        """Hot temperature suggests shorter interval."""
        decision = self.logic.decide_for_cluster(self.cluster_id, current_temp=30.0)

        assert decision is not None
        assert decision.action.value == "irrigate"
        assert decision.interval_hours < 8
        assert "temperature-based" in decision.reason_text

    def test_temperature_fallback_moderate(self):
        """Moderate temperature suggests medium interval."""
        decision = self.logic.decide_for_cluster(self.cluster_id, current_temp=22.0)

        assert decision is not None
        assert decision.action.value == "irrigate"
        assert decision.interval_hours > 10
        assert decision.interval_hours < 14

    def test_soil_moisture_dry(self):
        """Dry soil triggers irrigation."""
        self._add_soil_sensor(moisture=25.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.action.value == "irrigate"
        assert decision.confidence > 0.8
        reason_lower = decision.reason_text.lower()
        assert "soil" in reason_lower or "stress" in reason_lower

    def test_soil_moisture_adequate(self):
        """Adequate soil moisture skips irrigation."""
        self._add_soil_sensor(moisture=55.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.action.value == "skip"
        assert "adequate" in decision.reason_text.lower()

    def test_confidence_without_sensors(self):
        """Temperature-based decision has lower confidence."""
        decision = self.logic.decide_for_cluster(self.cluster_id, current_temp=22.0)
        assert decision.confidence < 0.7

    def test_confidence_with_sensors(self):
        """Sensor-based decision has higher confidence."""
        self._add_soil_sensor(moisture=30.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision.confidence > 0.7

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
        assert decision.interval_hours <= 8

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
        assert decision.interval_hours > 14

    def test_no_plants_returns_skip(self):
        """Cluster with no plants returns skip action."""
        empty_cluster = self.db.add_cluster("Empty Cluster")
        decision = self.logic.decide_for_cluster(empty_cluster)
        assert decision.action.value == "skip"
        assert "no plants" in decision.reason_text.lower()

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
        assert decision.action.value == "irrigate"
        assert "driest" in decision.reason_text.lower()

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
        assert decision.action.value == "irrigate"
        assert decision.duration_minutes == 1  # Short burst
        assert "conflict" in decision.reason_text.lower()
        assert decision.confidence < 0.7

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
        assert decision.action.value == "skip"
        assert "adequate" in decision.reason_text.lower()

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

        logic = IrrigationLogic(self.db, get_plant_database())
        decision = logic.decide_for_cluster(cluster_id)
        assert decision.action.value == "skip"
        assert "cooldown" in decision.reason_text.lower()

    def test_no_data_returns_low_confidence(self):
        """No sensors and no temp returns low confidence skip."""
        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision.action.value == "skip"
        assert decision.confidence <= 0.3

    # ── Humidity-based decision tests ───────────────────────────────────────

    def _add_full_sensor(self, moisture, temperature=22.0, env_humidity=None, light=None, device_id=FAKE_SENSOR_ID):
        """Helper: add a sensor with full reading data."""
        sensor_id = self.db.add_sensor(
            cluster_id=self.cluster_id,
            tuya_device_id=device_id,
            name="Full Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        self.db.add_sensor_reading(
            sensor_id=sensor_id,
            soil_moisture=moisture,
            temperature=temperature,
            env_humidity=env_humidity,
            light=light,
        )
        return sensor_id

    def test_very_dry_air_increases_frequency(self):
        """Very dry air (far below ideal) reduces interval."""
        # Plant ideal_humidity_min=60, so 30% is far below → very dry air
        self._add_full_sensor(moisture=50.0, env_humidity=30.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision is not None
        assert "dry air" in decision.reason_text.lower()
        # Should reduce interval by 3 hours from the default
        assert decision.interval_hours < 12

    def test_humid_air_increases_interval(self):
        """High ambient humidity increases interval (less transpiration)."""
        # Plant ideal_humidity_max=80, so 95% is far above → high humidity
        self._add_full_sensor(moisture=50.0, env_humidity=95.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision is not None
        assert "humidity" in decision.reason_text.lower()
        assert decision.interval_hours > 12

    def test_moderately_dry_air_adjustment(self):
        """Slightly dry air has smaller frequency adjustment."""
        # Plant ideal_humidity_min=60, so 50% is just below (60-5=55 threshold)
        self._add_full_sensor(moisture=50.0, env_humidity=50.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision is not None
        assert "dry air" in decision.reason_text.lower()

    # ── Light-based decision tests ──────────────────────────────────────────

    def test_very_bright_light_increases_frequency(self):
        """Very bright light reduces interval and increases duration."""
        # LIGHT_VERY_BRIGHT=1500, seasonal factor ~0.85 in April → ~1275
        self._add_full_sensor(moisture=50.0, light=2000)
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision is not None
        assert "bright" in decision.reason_text.lower()
        assert decision.interval_hours < 12

    def test_very_dark_light_decreases_frequency(self):
        """Very dark conditions increase interval."""
        # LIGHT_VERY_DARK=50 * seasonal ~0.85 = ~42. Use 20 (above night filter of 15)
        self._add_full_sensor(moisture=50.0, light=20)
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision is not None
        assert "light" in decision.reason_text.lower()
        assert decision.interval_hours > 12

    # ── Stress detection tests ──────────────────────────────────────────────

    def test_water_warning_highest_priority(self):
        """Sensor water_warning triggers irrigation with highest confidence."""
        sensor_id = self.db.add_sensor(
            cluster_id=self.cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name="Warning Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        self.db.add_sensor_reading(
            sensor_id=sensor_id,
            soil_moisture=50.0,
            water_warning=True,
        )
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.action.value == "irrigate"
        assert decision.confidence >= 0.9
        assert "sensor alert" in decision.reason_text.lower()

    def test_critical_moisture_triggers_stress(self):
        """Soil moisture below critical (30%) triggers water stress."""
        self._add_soil_sensor(moisture=20.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.action.value == "irrigate"
        assert decision.confidence >= 0.9
        stress = decision.stress_indicators.model_dump(exclude_none=True)
        assert "water_stress" in stress

    def test_over_watering_detected(self):
        """Saturated soil + high irrigation frequency triggers over-watering skip."""
        cluster_id = self.db.add_cluster("Overwater Cluster")
        self.db.add_plant(cluster_id=cluster_id, species=FAKE_PLANT_SPECIES, water_needs="medium")
        irrigator_id = self.db.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id="fake_irr_ow",
            name="Irrigator",
            irrigator_type="tuya_cloud",
            config={},
        )
        sensor_id = self.db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id="fake_sensor_ow",
            name="Wet Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        now = int(time.time())
        # Saturated soil readings
        self.db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=75.0, timestamp=now)
        # High irrigation frequency: >3 per day over last 7 days (events WITHIN 7 days)
        for i in range(25):
            self.db.add_irrigation_event(
                irrigator_id=irrigator_id,
                action="start",
                triggered_by="auto",
                duration_minutes=2,
                timestamp=now - (7 * 86400) + i * 3600,  # Within last 7 days, outside cooldown
            )

        logic = IrrigationLogic(self.db, get_plant_database())
        decision = logic.decide_for_cluster(cluster_id)

        assert decision.action.value == "skip"
        stress = decision.stress_indicators.model_dump(exclude_none=True)
        assert "over_watering" in stress or "over-watering" in decision.reason_text.lower()

    def test_heat_stress_above_ideal(self):
        """Temp far above ideal + rising trend triggers heat stress."""
        cluster_id = self.db.add_cluster("Heat Cluster")
        self.db.add_plant(
            cluster_id=cluster_id,
            species=FAKE_PLANT_SPECIES,
            water_needs="medium",
            ideal_temp_min=18.0,
            ideal_temp_max=27.0,
        )
        sensor_id = self.db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id="fake_sensor_heat",
            name="Hot Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        now = int(time.time())
        # Add readings showing high temp (well above ideal max of 27)
        self.db.add_sensor_reading(
            sensor_id=sensor_id,
            soil_moisture=50.0,
            temperature=35.0,
            timestamp=now,
        )

        logic = IrrigationLogic(self.db, get_plant_database())
        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        stress = decision.stress_indicators.model_dump(exclude_none=True)
        assert "heat_stress" in stress

    # ── Trend-based decision tests ──────────────────────────────────────────

    def test_declining_moisture_trend_reduces_interval(self):
        """Declining soil moisture trend reduces interval."""
        sensor_id = self.db.add_sensor(
            cluster_id=self.cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name="Trend Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        now = int(time.time())
        # First half: higher moisture, second half: lower moisture (decline > 5%)
        for i in range(8):
            moisture = 60.0 - (i * 3)  # 60, 57, 54, 51, 48, 45, 42, 39
            self.db.add_sensor_reading(
                sensor_id=sensor_id,
                soil_moisture=moisture,
                temperature=22.0,
                timestamp=now - (7 - i) * 3600,
            )

        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision is not None
        assert "declining" in decision.reason_text.lower()

    def test_rising_moisture_trend_increases_interval(self):
        """Rising soil moisture trend increases interval."""
        sensor_id = self.db.add_sensor(
            cluster_id=self.cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name="Rising Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        now = int(time.time())
        # First half: lower moisture, second half: higher moisture (rise > 5%)
        for i in range(8):
            moisture = 40.0 + (i * 3)  # 40, 43, 46, 49, 52, 55, 58, 61
            self.db.add_sensor_reading(
                sensor_id=sensor_id,
                soil_moisture=moisture,
                temperature=22.0,
                timestamp=now - (7 - i) * 3600,
            )

        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision is not None
        assert "rising" in decision.reason_text.lower()

    def test_config_fallback_manual_mode(self):
        """Config in manual mode returns skip with low confidence."""
        self.db.set_irrigation_config(
            cluster_id=self.cluster_id,
            mode="manual",
            duration_minutes=3,
            interval_hours=8,
        )
        # No sensors, no temp → should use config fallback
        decision = self.logic.decide_for_cluster(self.cluster_id)
        assert decision.action.value == "skip"
        assert decision.confidence == pytest.approx(0.3)

    def test_soil_too_wet_skips(self):
        """Soil moisture above target max skips irrigation."""
        self._add_soil_sensor(moisture=75.0)
        decision = self.logic.decide_for_cluster(self.cluster_id)

        assert decision.action.value == "skip"
        assert "wet" in decision.reason_text.lower()


class _StubWeatherClient:
    """Minimal weather client stub for engine tests."""

    def __init__(self, precipitation_mm: float = 0.0, current: dict | None = None):
        self._precip = precipitation_mm
        self._current = current or {}

    def get_forecast(self, hours: int = 6) -> dict:
        return {"precipitation_mm": self._precip, "max_temp": 20.0, "min_temp": 10.0, "avg_humidity": 70.0}

    def get_current(self) -> dict:
        return self._current


class TestWeatherSkipRule:
    """Tests for the weather-skip rule in the irrigation engine."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_db):
        self.db = tmp_db
        self.plant_db = get_plant_database()

    def _make_outdoor_cluster_with_sensor(self, moisture: float = 30.0) -> tuple[int, int]:
        cluster_id = self.db.add_cluster("Outdoor Cluster", environment="outdoor")
        self.db.add_plant(cluster_id=cluster_id, species=FAKE_PLANT_SPECIES, water_needs="medium")
        sensor_id = self.db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name="Outdoor Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        self.db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=moisture)
        return cluster_id, sensor_id

    def test_weather_skip_fires_on_outdoor_cluster_with_heavy_rain(self):
        """Significant rain forecast skips irrigation on outdoor cluster."""
        cluster_id, _ = self._make_outdoor_cluster_with_sensor(moisture=20.0)
        logic = IrrigationLogic(self.db, self.plant_db, weather_client=_StubWeatherClient(precipitation_mm=5.0))

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        assert decision.action.value == "skip"
        assert decision.primary_code.value == "weather_skip"
        assert "rain forecast" in decision.reason_text.lower()

    def test_weather_skip_does_not_fire_below_threshold(self):
        """Light rain (<= 2mm) does not trigger weather-skip."""
        cluster_id, _ = self._make_outdoor_cluster_with_sensor(moisture=20.0)
        logic = IrrigationLogic(self.db, self.plant_db, weather_client=_StubWeatherClient(precipitation_mm=1.0))

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        # Dry soil (20%) should still trigger irrigation, not weather-skip
        assert decision.action.value == "irrigate"

    def test_weather_skip_does_not_fire_on_indoor_cluster(self):
        """Indoor clusters are exempt from weather-skip regardless of forecast."""
        cluster_id = self.db.add_cluster("Indoor Cluster", environment="indoor")
        self.db.add_plant(cluster_id=cluster_id, species=FAKE_PLANT_SPECIES, water_needs="medium")
        sensor_id = self.db.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=FAKE_SENSOR_ID,
            name="Indoor Sensor",
            sensor_type="soil_moisture",
            config={},
        )
        self.db.add_sensor_reading(sensor_id=sensor_id, soil_moisture=20.0)
        logic = IrrigationLogic(self.db, self.plant_db, weather_client=_StubWeatherClient(precipitation_mm=10.0))

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        assert decision.action.value != "skip" or decision.primary_code.value != "weather_skip"
        # The engine should decide normally (irrigation, given dry soil)
        assert decision.action.value == "irrigate"

    def test_weather_skip_noop_when_no_weather_client(self):
        """No weather client → rule is skipped; engine proceeds normally."""
        cluster_id, _ = self._make_outdoor_cluster_with_sensor(moisture=20.0)
        logic = IrrigationLogic(self.db, self.plant_db)  # no weather_client

        decision = logic.decide_for_cluster(cluster_id)

        assert decision is not None
        assert decision.action.value == "irrigate"

    def test_weather_skip_sets_weather_snapshot(self):
        """Weather snapshot is populated on a weather-skip decision."""
        cluster_id, _ = self._make_outdoor_cluster_with_sensor(moisture=20.0)
        logic = IrrigationLogic(self.db, self.plant_db, weather_client=_StubWeatherClient(precipitation_mm=5.0))

        decision = logic.decide_for_cluster(cluster_id)

        assert decision.weather is not None
        assert decision.weather.precipitation_next_6h_mm == pytest.approx(5.0)
