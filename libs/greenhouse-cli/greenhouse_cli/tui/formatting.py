"""Small pure helpers that turn API payloads into display strings / Rich text."""

from __future__ import annotations

import time
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich.text import Text

SEVERITY_STYLES = {
    "critical": "bold #ff5f5f",
    "error": "bold #ff5f5f",
    "warning": "#e0c341",
    "info": "#6fb7ff",
    "success": "#7ed957",
}

ACTION_STYLES = {
    "irrigate": "bold #4fb3ff",
    "start": "bold #4fb3ff",
    "skip": "#9a9a9a",
    "stop": "#e0663f",
    "monitor": "#9bcf5a",
}

STATUS_STYLES = {
    "ok": "bold #7ed957",
    "fresh": "#7ed957",
    "degraded": "bold #e0c341",
    "stale": "#e0c341",
    "down": "bold #ff5f5f",
    "cold": "#ff5f5f",
    "open": "bold #ff5f5f",
    "acknowledged": "#e0c341",
    "resolved": "#7ed957",
}

METRICS: dict[str, tuple[str, str]] = {
    # metric key: (label, unit)
    "soil_moisture": ("Soil moisture", "%"),
    "temperature": ("Temperature", "°C"),
    "env_humidity": ("Air humidity", "%"),
    "light": ("Light", "lx"),
}


def now() -> int:
    return int(time.time())


def _span(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600}h"


def ago(ts: int | float | None, reference: int | None = None) -> str:
    """Render a Unix timestamp as a compact relative age (``5m ago`` / ``in 2h00m``)."""
    if not ts:
        return "never"
    delta = int((reference if reference is not None else now()) - ts)
    return f"in {_span(-delta)}" if delta < 0 else f"{_span(delta)} ago"


def age(seconds: int | float | None) -> str:
    """Render an age already expressed in seconds (``None`` → ``—``)."""
    if seconds is None:
        return "—"
    return f"{_span(max(0, int(seconds)))} ago"


def zone(name: str | None) -> tzinfo:
    """The IANA zone ``name`` (the server's ``timezone`` preference); UTC when unset or unknown."""
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def clock(ts: int | float | None, with_date: bool = False, *, tz: str | None = None) -> str:
    """Wall-clock time for a Unix timestamp — local, or in the zone ``tz`` when given (vacations)."""
    if not ts:
        return "—"
    fmt = "%Y-%m-%d %H:%M" if with_date else "%H:%M"
    return datetime.fromtimestamp(ts, zone(tz) if tz is not None else None).strftime(fmt)


def num(value: float | int | None, unit: str = "", digits: int = 1) -> str:
    """Format a nullable number with a unit."""
    if value is None:
        return "—"
    if isinstance(value, int) or digits == 0:
        return f"{round(value)}{unit}"
    return f"{value:.{digits}f}{unit}"


def styled(value: str | None, styles: dict[str, str], default: str = "") -> Text:
    """Return ``value`` as Text styled by a lookup table."""
    text = value or "—"
    return Text(text, style=styles.get((value or "").lower(), default))


def bar(value: float | None, width: int = 20, lo: float | None = None, hi: float | None = None) -> Text:
    """A horizontal 0–100 gauge with the ideal band marked underneath the fill.

    Args:
        value: Percentage to draw, or ``None`` for an empty gauge.
        width: Gauge width in cells.
        lo: Lower edge of the ideal band (drawn as a tick).
        hi: Upper edge of the ideal band (drawn as a tick).
    """
    text = Text()
    if value is None:
        return Text("░" * width, style="#555555")
    filled = max(0, min(width, round(value / 100 * width)))
    lo_i = None if lo is None else max(0, min(width - 1, round(lo / 100 * width)))
    hi_i = None if hi is None else max(0, min(width - 1, round(hi / 100 * width)))
    if lo is not None and value < lo:
        fill_style = "#e0663f" if value < lo - 10 else "#e0c341"
    elif hi is not None and value > hi:
        fill_style = "#4fb3ff"
    else:
        fill_style = "#3fa34d"
    for i in range(width):
        in_band = lo_i is not None and hi_i is not None and lo_i <= i <= hi_i
        if i < filled:
            text.append("█", style=fill_style)
        elif i in (lo_i, hi_i):
            text.append("┊", style="#7ed957")
        else:
            text.append("░", style="#2f4f2f" if in_band else "#444444")
    return text


def weekday_mask(mask: int) -> str:
    """Render a Mon-bit-1 weekday bitmask as ``MTWTF··``."""
    if mask == 127:
        return "every day"
    letters = "MTWTFSS"
    return "".join(letters[i] if mask & (1 << i) else "·" for i in range(7))
