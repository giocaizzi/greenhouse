#!/usr/bin/env python3
"""Utility functions for irrigation system."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo


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
