#!/usr/bin/env python3
"""Utility functions for irrigation system."""

import os
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from greenhouse_core.constants import NIGHT_LUX_THRESHOLD, SEASONAL_LIGHT_FACTOR_BY_MONTH

# Seasonal light reduction factor by month (Northern hemisphere, ~45°N latitude - Milano);
# the table lives in constants. NIGHT_LUX_THRESHOLD stays importable from here.
_SEASONAL_LIGHT_FACTOR: dict[int, float] = SEASONAL_LIGHT_FACTOR_BY_MONTH


def seasonal_light_factor(month: int | None = None) -> float:
    """Return the seasonal light reduction factor for a given month (1-12).

    Factor reflects available natural daylight relative to peak summer (1.0).
    Accounts for shorter days and lower sun angle in winter at ~45°N (Milano).

    If month is None, uses the current month.
    """
    if month is None:
        month = datetime.now(tz=UTC).month
    return _SEASONAL_LIGHT_FACTOR.get(month, 1.0)


def daytime_lux_readings(readings: list[Any], min_lux: int = NIGHT_LUX_THRESHOLD) -> list[float]:
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


# The authoritative display timezone is ``UserPreferences.timezone`` — the same
# zone the decision engine gates windows against. The server records it here at
# startup and whenever the preference changes (see
# ``greenhouse_server.scheduler`` / the preferences routes) so the stateless
# Jinja ``format_ts`` filter and other display-only callers stay on one clock
# without threading prefs through every call site. ``IRRIGATION_TZ`` survives
# only as the startup fallback default, never as a competing live source.
_display_timezone: str | None = None


def set_display_timezone(tz_name: str | None) -> None:
    """Record the authoritative display timezone (``UserPreferences.timezone``).

    Idempotent. A falsy value clears the override, so ``get_display_timezone``
    falls back to ``IRRIGATION_TZ`` then UTC.
    """
    global _display_timezone
    _display_timezone = tz_name or None


def get_display_timezone() -> str:
    """Get the timezone to use for displaying timestamps.

    Precedence: the recorded ``UserPreferences.timezone`` (via
    :func:`set_display_timezone`) > the ``IRRIGATION_TZ`` env fallback > UTC.
    """
    if _display_timezone:
        return _display_timezone
    return os.getenv("IRRIGATION_TZ", "UTC")


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
        dt_utc = datetime.fromtimestamp(timestamp, tz=UTC)
        return dt_utc.strftime(fmt) + " UTC"
