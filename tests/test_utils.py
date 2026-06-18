"""Test suite for utility functions."""

import pytest

from greenhouse_core.utils import (
    daytime_lux_readings,
    effective_light_threshold,
    format_timestamp,
    get_display_timezone,
    seasonal_light_factor,
    set_display_timezone,
)


@pytest.fixture(autouse=True)
def _reset_display_tz():
    """Keep the process-level display tz from leaking across tests."""
    set_display_timezone(None)
    yield
    set_display_timezone(None)


class TestSeasonalLightFactor:
    def test_summer_peak(self):
        """June and July return 1.0 (peak)."""
        assert seasonal_light_factor(6) == 1.0
        assert seasonal_light_factor(7) == 1.0

    def test_winter_minimum(self):
        """December and January return lowest factor."""
        assert seasonal_light_factor(12) == 0.50
        assert seasonal_light_factor(1) == 0.50

    def test_spring_increases(self):
        """Factor increases from winter to summer."""
        assert seasonal_light_factor(2) < seasonal_light_factor(4) < seasonal_light_factor(6)

    def test_fall_decreases(self):
        """Factor decreases from summer to winter."""
        assert seasonal_light_factor(8) > seasonal_light_factor(10) > seasonal_light_factor(12)

    def test_current_month_default(self):
        """None defaults to current month without error."""
        result = seasonal_light_factor()
        assert 0.0 < result <= 1.0

    def test_all_months_valid(self):
        """All months return a value in (0, 1]."""
        for month in range(1, 13):
            factor = seasonal_light_factor(month)
            assert 0.0 < factor <= 1.0


class TestDaytimeLuxReadings:
    def test_filters_night_readings(self):
        """Readings at or below threshold are excluded."""

        class FakeReading:
            def __init__(self, light):
                self.light = light

        readings = [FakeReading(0), FakeReading(10), FakeReading(15), FakeReading(100), FakeReading(500)]
        result = daytime_lux_readings(readings)
        assert result == [100.0, 500.0]

    def test_handles_none_light(self):
        """Readings with None light are excluded."""

        class FakeReading:
            def __init__(self, light):
                self.light = light

        readings = [FakeReading(None), FakeReading(200)]
        result = daytime_lux_readings(readings)
        assert result == [200.0]

    def test_empty_list(self):
        """Empty list returns empty."""
        assert daytime_lux_readings([]) == []


class TestEffectiveLightThreshold:
    def test_summer_full_threshold(self):
        """In summer, threshold equals base."""
        assert effective_light_threshold(800, month=6) == 800.0

    def test_winter_reduced_threshold(self):
        """In winter, threshold is reduced."""
        threshold = effective_light_threshold(800, month=12)
        assert threshold == pytest.approx(400.0)

    def test_zero_base(self):
        """Zero base returns zero regardless of season."""
        assert effective_light_threshold(0, month=6) == 0.0


class TestFormatTimestamp:
    def test_formats_unix_timestamp(self):
        """Timestamp is formatted to human-readable string."""
        result = format_timestamp(0)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_custom_format(self):
        """Custom format string is respected."""
        result = format_timestamp(0, fmt="%Y")
        assert result == "1970"


class TestGetDisplayTimezone:
    def test_default_timezone(self, monkeypatch):
        """With no preference set and no env override, the default is UTC."""
        monkeypatch.delenv("IRRIGATION_TZ", raising=False)
        assert get_display_timezone() == "UTC"

    def test_env_fallback(self, monkeypatch):
        """IRRIGATION_TZ is the fallback when no preference is recorded."""
        monkeypatch.setenv("IRRIGATION_TZ", "America/New_York")
        assert get_display_timezone() == "America/New_York"

    def test_preference_wins_over_env(self, monkeypatch):
        """A recorded UserPreferences.timezone outranks the env fallback."""
        monkeypatch.setenv("IRRIGATION_TZ", "America/New_York")
        set_display_timezone("Asia/Tokyo")
        assert get_display_timezone() == "Asia/Tokyo"

    def test_clearing_preference_falls_back(self, monkeypatch):
        """Clearing the preference re-exposes the env fallback."""
        monkeypatch.setenv("IRRIGATION_TZ", "America/New_York")
        set_display_timezone("Asia/Tokyo")
        set_display_timezone(None)
        assert get_display_timezone() == "America/New_York"
