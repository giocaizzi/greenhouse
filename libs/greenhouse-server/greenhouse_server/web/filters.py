"""Jinja2 template filters for the web UI."""

from __future__ import annotations

import re
import time
from typing import Any

from greenhouse_core.constants import AGE_BADGE_STALE_SECONDS, SECONDS_PER_DAY, SECONDS_PER_HOUR
from greenhouse_core.utils import format_timestamp

# Matches common Unicode emoji ranges. Used to scrub decorative glyphs out of
# server-emitted reason strings so the UI's icon system stays the only voice.
_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001f6ff\U0001f900-\U0001f9ff\U0001fa70-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f02f✀-➿️]+",
    flags=re.UNICODE,
)

_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60


def format_ts(ts: int | float | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Template filter: a Unix timestamp in the display timezone (``—`` when missing)."""
    if ts is None:
        return "—"
    return format_timestamp(float(ts), fmt)


def relative_age(
    ts: int | float | None, *, missing: str = "—", stale_after: int | None = AGE_BADGE_STALE_SECONDS
) -> str:
    """The one "how long ago" formatter: ``Ns`` / ``Nm`` / ``Nh`` / ``Nd ago`` for a Unix timestamp.

    ``missing`` is shown for ``None``; from ``stale_after`` seconds on the age reads
    "stale" (sensor freshness), or keeps counting days when ``stale_after`` is ``None``
    (elapsed time, e.g. the plant dashboard's last watering).
    """
    if ts is None:
        return missing
    delta = max(0, int(time.time() - float(ts)))
    if delta < _SECONDS_PER_MINUTE:
        return f"{delta}s ago"
    if delta < SECONDS_PER_HOUR:
        return f"{delta // _SECONDS_PER_MINUTE}m ago"
    if delta < SECONDS_PER_DAY:
        return f"{delta // SECONDS_PER_HOUR}h ago"
    if stale_after is None or delta < stale_after:
        return f"{delta // SECONDS_PER_DAY}d ago"
    return "stale"


def age_seconds(ts: int | float | None) -> str:
    """Template filter: :func:`relative_age` with the freshness defaults ("—" when missing, "stale" from 7 days)."""
    return relative_age(ts)


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
    """Template filter: badge class for a soil-moisture value against its target band."""
    if value is None:
        return "muted"
    if target_min is not None and value < target_min:
        return "low"
    if target_max is not None and value > target_max:
        return "high"
    return "ok"


def severity_class(severity: str | None) -> str:
    """Template filter: CSS class for an alert/insight severity (``muted`` when unknown)."""
    return {"critical": "danger", "warning": "warning", "info": "info"}.get((severity or "").lower(), "muted")


def decision_badge(action: str | None) -> str:
    """Template filter: badge class for a decision action (``muted`` when unknown)."""
    return {"irrigate": "primary", "hold": "muted", "skip": "muted", "error": "danger"}.get(
        (action or "").lower(), "muted"
    )


def format_minutes(n: int | None) -> str:
    """Template filter: a duration in minutes as ``N min`` / ``Nh`` / ``Nh Mm`` (``—`` when missing)."""
    if n is None:
        return "—"
    if n < _MINUTES_PER_HOUR:
        return f"{n} min"
    h, m = divmod(n, _MINUTES_PER_HOUR)
    return f"{h}h {m}m" if m else f"{h}h"


def yesno(value: object, yes: str = "Yes", no: str = "No") -> str:
    """Template filter: ``yes`` / ``no`` label for a truthy / falsy value."""
    return yes if value else no


_TRIGGER_CODE_ICONS: dict[str, str] = {
    # Terminal triggers
    "no_plants": "leaf",
    "cooldown": "clock",
    "leak_hold": "x-circle",
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


def _present(value: Any) -> bool:
    """True when a child collection/relationship holds at least one item.

    Accepts the shapes the web layer passes around: ``status`` lists, a single
    ORM object or ``None`` (the ``uselist=False`` irrigator), or an ORM
    relationship collection. ``None`` and empty collections are absent.
    """
    if value is None:
        return False
    try:
        return len(value) > 0
    except TypeError:
        return bool(value)


def cluster_caps(obj: Any) -> dict[str, Any]:
    """Derive a cluster's capability tier from what it contains.

    The single source of truth for feature gating across the web UI. Accepts
    either a ``get_cluster_status`` dict (``plants``/``sensors``/``irrigator``
    keys) or a plain ``Cluster`` ORM object (same-named relationships), so the
    detail page, the home cards, and the clusters list all gate identically.

    The tier is driven by the actuation axis (irrigator present); the
    ``can_*`` booleans gate sub-sections so the cascade composes:

    - ``can_monitor`` (≥1 sensor) → live readings, charts, live-status panel.
    - ``can_target`` (≥1 plant) → target bands on tiles, health scoring.
    - ``can_decide`` (sensors ∧ plants) → decision hero, rationale, re-evaluate.
    - ``can_actuate`` (irrigator) → Irrigate, config behaviour, windows,
      irrigation heatmap, and the Stats/Decisions/Efficacy/Learn tabs.

    Args:
        obj: A cluster-status dict, a ``Cluster`` ORM object, or ``None``.

    Returns:
        Dict with ``has_plants``/``has_sensors``/``has_irrigator``, the four
        ``can_*`` booleans, ``tier`` (``"empty"``/``"sensor_only"``/
        ``"operational"``), and ``missing`` — prerequisites ordered by impact
        for the next-step notice.
    """
    if isinstance(obj, dict):
        plants, sensors, irrigator = obj.get("plants"), obj.get("sensors"), obj.get("irrigator")
    elif obj is None:
        plants = sensors = irrigator = None
    else:  # Cluster ORM object
        plants = getattr(obj, "plants", None)
        sensors = getattr(obj, "sensors", None)
        irrigator = getattr(obj, "irrigator", None)

    has_plants, has_sensors, has_irrigator = _present(plants), _present(sensors), _present(irrigator)

    if has_irrigator:
        tier = "operational"
    elif has_plants or has_sensors:
        tier = "sensor_only"
    else:
        tier = "empty"

    # Ordered by what unblocks the most: actuation first (the cluster can't
    # water at all without it), then monitoring, then targets.
    missing = []
    if not has_irrigator:
        missing.append("irrigator")
    if not has_sensors:
        missing.append("sensors")
    if not has_plants:
        missing.append("plants")

    return {
        "has_plants": has_plants,
        "has_sensors": has_sensors,
        "has_irrigator": has_irrigator,
        "can_monitor": has_sensors,
        "can_target": has_plants,
        "can_decide": has_sensors and has_plants,
        "can_actuate": has_irrigator,
        "tier": tier,
        "missing": missing,
    }


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
    "cluster_caps": cluster_caps,
}
