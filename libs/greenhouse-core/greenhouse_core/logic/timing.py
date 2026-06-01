"""Local-time gating and seasonal multipliers for the decision engine.

Two concerns live here:
1. **Windows** — given a list of IrrigationWindow rows and a timezone, decide
   whether the current local time lies inside an allowed window.
2. **Seasons** — derive the meteorological season from a date and a hemisphere,
   then resolve a per-plant frequency multiplier (used to scale interval_hours).

Everything is a pure function so the engine remains independently testable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from greenhouse_core.constants import (
    DEFAULT_PREFERRED_WATER_HOURS,
    DEFAULT_SEASON_MULTIPLIER_INDOOR,
    DEFAULT_SEASON_MULTIPLIER_OUTDOOR,
)
from greenhouse_core.models import IrrigationWindow

Season = Literal["winter", "spring", "summer", "autumn"]
Environment = Literal["indoor", "outdoor"]
Hemisphere = Literal["northern", "southern"]


def _resolve_tz(tz_name: str | None) -> ZoneInfo:
    """Best-effort tz resolution; falls back to UTC for missing or bad names."""
    if not tz_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def local_now(unix_ts: int, tz_name: str | None) -> datetime:
    """Convert a unix timestamp to a local-time datetime."""
    return datetime.fromtimestamp(unix_ts, tz=_resolve_tz(tz_name))


def _weekday_bit(dt: datetime) -> int:
    """1-bit-Mon mask for the local-time weekday — Mon=1, Tue=2, …, Sun=64."""
    return 1 << dt.weekday()


def _hour_in_range(hour: int, start: int, end: int) -> bool:
    """True if ``hour`` ∈ [start, end). Handles wrap-around (e.g. 22..6)."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Wrap around midnight: 22..6 means 22,23,0,1,2,3,4,5.
    return hour >= start or hour < end


def is_within_irrigation_window(
    windows: list[IrrigationWindow],
    *,
    now_unix: int,
    tz_name: str | None,
) -> bool:
    """Return True if at least one configured window matches the current local time.

    A cluster with NO windows configured is treated as "always allowed" — the
    caller layers default preferred-hours on top via ``is_within_preferred_hours``.
    """
    if not windows:
        return True
    dt = local_now(now_unix, tz_name)
    bit = _weekday_bit(dt)
    for w in windows:
        if not (w.weekday_mask & bit):
            continue
        if _hour_in_range(dt.hour, w.start_hour, w.end_hour):
            return True
    return False


def is_within_preferred_hours(
    *,
    now_unix: int,
    tz_name: str | None,
    preferred: tuple[int, int] | None = None,
) -> bool:
    """Default soft window when no IrrigationWindow rows exist.

    ``preferred`` is (start_hour, end_hour) end-exclusive. None → use the global
    default (morning window from constants).
    """
    start, end = preferred or DEFAULT_PREFERRED_WATER_HOURS
    dt = local_now(now_unix, tz_name)
    return _hour_in_range(dt.hour, start, end)


def is_within_quiet_hours(
    *,
    start_hour: int | None,
    end_hour: int | None,
    now_unix: int,
    tz_name: str | None,
) -> bool:
    """True if the current local hour falls inside the quiet-hours window.

    ``start_hour == end_hour`` (including the common 0/0 case from disabled
    overrides) means quiet hours are switched off — returns False. None for
    either bound also means "no window" and returns False. Wrap-around
    (start > end) is supported, so 22..6 spans 22, 23, 0, 1, …, 5.
    """
    if start_hour is None or end_hour is None:
        return False
    if start_hour == end_hour:
        return False
    dt = local_now(now_unix, tz_name)
    return _hour_in_range(dt.hour, start_hour, end_hour)


def season_for(unix_ts: int, *, tz_name: str | None, hemisphere: Hemisphere = "northern") -> Season:
    """Meteorological season from a unix timestamp.

    Northern: winter=Dec/Jan/Feb, spring=Mar/Apr/May, summer=Jun/Jul/Aug,
    autumn=Sep/Oct/Nov. Southern hemisphere flips by six months.
    """
    month = local_now(unix_ts, tz_name).month
    if hemisphere == "southern":
        month = ((month - 1 + 6) % 12) + 1
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def seasonal_multiplier(
    season: Season,
    *,
    environment: Environment = "indoor",
    plant_override: dict | None = None,
    category_override: dict | None = None,
) -> float:
    """Resolve the per-season frequency multiplier.

    Precedence: plant-level override > category-level override > built-in default
    keyed on environment. Missing keys fall through to the next layer.
    """
    if plant_override:
        if (val := plant_override.get(season)) is not None:
            return float(val)
    if category_override:
        if (val := category_override.get(season)) is not None:
            return float(val)
    table = DEFAULT_SEASON_MULTIPLIER_OUTDOOR if environment == "outdoor" else DEFAULT_SEASON_MULTIPLIER_INDOOR
    return float(table.get(season, 1.0))
