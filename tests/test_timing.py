"""Unit tests for the timing helpers — windows + seasons + multipliers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from greenhouse_core.logic.timing import (
    is_within_irrigation_window,
    is_within_preferred_hours,
    season_for,
    seasonal_multiplier,
)
from greenhouse_core.models import IrrigationWindow


def _ts(year, month, day, hour, tz="UTC"):
    return int(datetime(year, month, day, hour, tzinfo=ZoneInfo(tz)).timestamp())


def test_preferred_hours_default_morning_window():
    # 07:00 UTC on a Thursday — within default 6..10 window.
    ts = _ts(2026, 5, 14, 7)
    assert is_within_preferred_hours(now_unix=ts, tz_name="UTC") is True


def test_preferred_hours_rejects_midday():
    ts = _ts(2026, 5, 14, 14)
    assert is_within_preferred_hours(now_unix=ts, tz_name="UTC") is False


def test_preferred_hours_custom_window():
    ts = _ts(2026, 5, 14, 17)
    # 16..19 secondary window
    assert is_within_preferred_hours(now_unix=ts, tz_name="UTC", preferred=(16, 19)) is True


def test_window_match_within_window():
    w = IrrigationWindow(id=1, cluster_id=1, weekday_mask=127, start_hour=6, end_hour=10)
    ts = _ts(2026, 5, 14, 7)
    assert is_within_irrigation_window([w], now_unix=ts, tz_name="UTC") is True


def test_window_match_outside_hours():
    w = IrrigationWindow(id=1, cluster_id=1, weekday_mask=127, start_hour=6, end_hour=10)
    ts = _ts(2026, 5, 14, 14)
    assert is_within_irrigation_window([w], now_unix=ts, tz_name="UTC") is False


def test_window_weekday_mask_filters_day():
    # Mon-only mask (bit 1)
    w = IrrigationWindow(id=1, cluster_id=1, weekday_mask=1, start_hour=6, end_hour=10)
    # 2026-05-14 is a Thursday — not Monday — should fail even though hours match.
    ts = _ts(2026, 5, 14, 7)
    assert is_within_irrigation_window([w], now_unix=ts, tz_name="UTC") is False
    # 2026-05-11 is a Monday — should match.
    ts_mon = _ts(2026, 5, 11, 7)
    assert is_within_irrigation_window([w], now_unix=ts_mon, tz_name="UTC") is True


def test_window_wrap_around_midnight():
    # 22..06 means 22,23,0..5
    w = IrrigationWindow(id=1, cluster_id=1, weekday_mask=127, start_hour=22, end_hour=6)
    assert is_within_irrigation_window([w], now_unix=_ts(2026, 5, 14, 23), tz_name="UTC") is True
    assert is_within_irrigation_window([w], now_unix=_ts(2026, 5, 14, 3), tz_name="UTC") is True
    assert is_within_irrigation_window([w], now_unix=_ts(2026, 5, 14, 10), tz_name="UTC") is False


def test_no_windows_treated_as_allowed():
    assert is_within_irrigation_window([], now_unix=_ts(2026, 5, 14, 3), tz_name="UTC") is True


def test_multiple_windows_or_logic():
    morning = IrrigationWindow(id=1, cluster_id=1, weekday_mask=127, start_hour=6, end_hour=10)
    evening = IrrigationWindow(id=2, cluster_id=1, weekday_mask=127, start_hour=17, end_hour=19)
    assert is_within_irrigation_window([morning, evening], now_unix=_ts(2026, 5, 14, 18), tz_name="UTC") is True
    assert is_within_irrigation_window([morning, evening], now_unix=_ts(2026, 5, 14, 12), tz_name="UTC") is False


def test_season_for_northern():
    assert season_for(_ts(2026, 1, 15, 12), tz_name="UTC") == "winter"
    assert season_for(_ts(2026, 4, 15, 12), tz_name="UTC") == "spring"
    assert season_for(_ts(2026, 7, 15, 12), tz_name="UTC") == "summer"
    assert season_for(_ts(2026, 10, 15, 12), tz_name="UTC") == "autumn"


def test_season_for_southern_flips():
    assert season_for(_ts(2026, 1, 15, 12), tz_name="UTC", hemisphere="southern") == "summer"
    assert season_for(_ts(2026, 7, 15, 12), tz_name="UTC", hemisphere="southern") == "winter"


def test_seasonal_multiplier_indoor_defaults():
    assert seasonal_multiplier("winter", environment="indoor") == 0.5
    assert seasonal_multiplier("spring", environment="indoor") == 1.0
    assert seasonal_multiplier("summer", environment="indoor") == 1.2


def test_seasonal_multiplier_outdoor_defaults():
    assert seasonal_multiplier("winter", environment="outdoor") == 0.3
    assert seasonal_multiplier("summer", environment="outdoor") == 1.5


def test_seasonal_multiplier_plant_override():
    override = {"winter": 0.25, "summer": 1.0}
    assert seasonal_multiplier("winter", environment="indoor", plant_override=override) == 0.25
    # Fallback when override does not have the season
    assert seasonal_multiplier("autumn", environment="indoor", plant_override=override) == 0.8


def test_seasonal_multiplier_category_fallback():
    cat = {"winter": 0.7}
    assert seasonal_multiplier("winter", environment="indoor", category_override=cat) == 0.7
    # Plant override beats category
    plant = {"winter": 0.4}
    assert seasonal_multiplier("winter", environment="indoor", plant_override=plant, category_override=cat) == 0.4
