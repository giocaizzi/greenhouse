"""Jinja2 template filters for the web UI."""

from __future__ import annotations

import time

from tuya_irrigation_core.utils import format_timestamp


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
    return f"{delta // 86400}d ago"


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


ALL_FILTERS = {
    "format_ts": format_ts,
    "age_seconds": age_seconds,
    "moisture_badge": moisture_badge,
    "severity_class": severity_class,
    "decision_badge": decision_badge,
    "format_minutes": format_minutes,
    "yesno": yesno,
}
