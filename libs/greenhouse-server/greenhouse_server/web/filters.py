"""Jinja2 template filters for the web UI."""

from __future__ import annotations

import re
import time

from greenhouse_core.utils import format_timestamp

# Matches common Unicode emoji ranges. Used to scrub decorative glyphs out of
# server-emitted reason strings so the UI's icon system stays the only voice.
_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001f6ff\U0001f900-\U0001f9ff\U0001fa70-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f02f✀-➿️]+",
    flags=re.UNICODE,
)

# Past this age we treat readings as "stale" — avoids absurd values like
# "20567d ago" leaking into the UI from seed data or long-offline sensors.
_AGE_STALE_SECONDS = 7 * 86400


def format_ts(ts: int | float | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if ts is None:
        return "—"
    return format_timestamp(float(ts), fmt)


def age_seconds(ts: int | float | None) -> str:
    if ts is None:
        return "—"
    delta = max(0, int(time.time() - float(ts)))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    if delta < _AGE_STALE_SECONDS:
        return f"{delta // 86400}d ago"
    return "stale"


def strip_emoji(text: str | None) -> str:
    """Remove decorative emoji from a string and collapse leftover whitespace."""
    if not text:
        return ""
    cleaned = _EMOJI_RE.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s*;\s*", "; ", cleaned)
    return cleaned


def stat_position(value: float | None, lo: float | None, hi: float | None) -> str:
    """Return the position of value as a 0–100 percent within [lo, hi].

    Used by the stat tile range indicator. Falls back to 50 when bounds are
    missing or value is out of range, so the marker stays visible.
    """
    if value is None or lo is None or hi is None or hi <= lo:
        return "50"
    pct = (float(value) - float(lo)) / (float(hi) - float(lo)) * 100
    return f"{max(0, min(100, pct)):.0f}"


def moisture_badge(value: float | None, target_min: float | None, target_max: float | None) -> str:
    if value is None:
        return "muted"
    if target_min is not None and value < target_min:
        return "low"
    if target_max is not None and value > target_max:
        return "high"
    return "ok"


def severity_class(severity: str | None) -> str:
    return {"critical": "danger", "warning": "warning", "info": "info"}.get((severity or "").lower(), "muted")


def decision_badge(action: str | None) -> str:
    return {"irrigate": "primary", "hold": "muted", "skip": "muted", "error": "danger"}.get(
        (action or "").lower(), "muted"
    )


def format_minutes(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 60:
        return f"{n} min"
    h, m = divmod(n, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def yesno(value, yes: str = "Yes", no: str = "No") -> str:
    return yes if value else no


_TRIGGER_CODE_ICONS: dict[str, str] = {
    # Terminal triggers
    "no_plants": "leaf",
    "cooldown": "clock",
    "water_warning": "warning",
    "water_stress": "drop",
    "over_watering": "drop",
    "sensor_very_dry": "drop",
    "sensor_dry": "drop",
    "sensor_adequate": "check",
    "sensor_wet": "drop",
    "conflict": "scales",
    "weather_skip": "cloud-rain",
    "temp_fallback": "thermometer-simple",
    "config_fallback": "gear-six",
    "no_data": "x-circle",
    "daily_cap_hit": "x-circle",
    # Adjustments
    "temp_high": "thermometer-hot",
    "temp_low": "thermometer-simple",
    "humidity_very_low": "warning",
    "humidity_low": "warning",
    "humidity_high": "cloud-rain",
    "light_very_bright": "sun",
    "light_bright": "sun",
    "light_dark": "moon",
    "light_very_dark": "moon",
    "water_needs_high": "drop",
    "water_needs_low": "drop",
    "trend_moisture_declining": "trend-down",
    "trend_moisture_rising": "chart-line-up",
    "trend_temp_rising": "thermometer-hot",
    "underwatering_pattern": "warning",
    "learning_alert": "info",
}

# Severity-to-icon fallback used for CareInsight cards
_SEVERITY_ICONS: dict[str, str] = {
    "critical": "x-circle",
    "warning": "warning",
    "info": "info",
}


def icon_for_code(code: str) -> str:
    """Map a TriggerCode value to a sprite icon id (without the ``i-`` prefix)."""
    return _TRIGGER_CODE_ICONS.get(code, _SEVERITY_ICONS.get(code, "info"))


ALL_FILTERS = {
    "format_ts": format_ts,
    "age_seconds": age_seconds,
    "moisture_badge": moisture_badge,
    "severity_class": severity_class,
    "decision_badge": decision_badge,
    "format_minutes": format_minutes,
    "yesno": yesno,
    "strip_emoji": strip_emoji,
    "stat_position": stat_position,
    "icon_for_code": icon_for_code,
}
