#!/usr/bin/env python3
"""Utility functions for irrigation system."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

# Seasonal light reduction factor by month (Northern hemisphere, ~45°N latitude - Milano)
# Represents typical ratio of available daylight vs peak summer.
# June/July = 1.0 (peak). December/January = ~0.55 (shortest days + low sun angle).
_SEASONAL_LIGHT_FACTOR: dict[int, float] = {
    1: 0.50,
    2: 0.60,
    3: 0.72,
    4: 0.85,
    5: 0.95,
    6: 1.00,
    7: 1.00,
    8: 0.95,
    9: 0.83,
    10: 0.70,
    11: 0.58,
    12: 0.50,
}

# Lux threshold below which a reading is considered "nighttime / artificial light only"
# Used to exclude night readings from daytime light averages.
NIGHT_LUX_THRESHOLD = 15


def seasonal_light_factor(month: int | None = None) -> float:
    """Return the seasonal light reduction factor for a given month (1-12).

    Factor reflects available natural daylight relative to peak summer (1.0).
    Accounts for shorter days and lower sun angle in winter at ~45°N (Milano).

    If month is None, uses the current month.
    """
    if month is None:
        month = datetime.now().month
    return _SEASONAL_LIGHT_FACTOR.get(month, 1.0)


def daytime_lux_readings(readings: list, min_lux: int = NIGHT_LUX_THRESHOLD) -> list[float]:
    """Extract daytime lux values from a list of SensorReading objects.

    Filters out readings where light <= min_lux (night / no light).
    Returns a list of float lux values (may be empty).
    """
    return [float(r.light) for r in readings if r.light is not None and r.light > min_lux]


def effective_light_threshold(base_lux: float, month: int | None = None) -> float:
    """Compute the seasonally-adjusted light threshold.

    A plant that needs 800 lux in summer should only need ~400 lux in December
    to be considered adequately lit, because natural light IS lower in winter.
    The threshold scales with the seasonal factor.

    Args:
        base_lux: the plant's minimum lux requirement (summer baseline)
        month: override month (1-12); defaults to current month
    """
    return base_lux * seasonal_light_factor(month)


def get_display_timezone() -> str:
    """
    Get the timezone to use for displaying timestamps.

    Reads from IRRIGATION_TZ environment variable, defaults to Europe/Rome.
    """
    return os.getenv("IRRIGATION_TZ", "Europe/Rome")


def format_timestamp(timestamp: float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """
    Format a UTC timestamp for display in local timezone.

    Args:
        timestamp: Unix timestamp (UTC)
        fmt: strftime format string

    Returns:
        Formatted timestamp string in local timezone
    """
    tz = get_display_timezone()
    try:
        dt_utc = datetime.fromtimestamp(timestamp, tz=ZoneInfo("UTC"))
        dt_local = dt_utc.astimezone(ZoneInfo(tz))
        return dt_local.strftime(fmt)
    except Exception:
        # Fallback to UTC if timezone conversion fails
        dt_utc = datetime.utcfromtimestamp(timestamp)
        return dt_utc.strftime(fmt) + " UTC"
