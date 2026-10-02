"""Pure TUI builders: API payload → table rows, key/value rows, rich text (no widgets, no I/O; golden-pinned)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import RenderableType
from rich.text import Text

from greenhouse_cli.tui import formatting as fmt

if TYPE_CHECKING:
    from greenhouse_cli.tui.model import ClusterSummary

Row = tuple[str | None, list[RenderableType | str]]
"""One ``DataTable`` row for :func:`greenhouse_cli.tui.widgets.refill`: ``(row key or None, cells)``."""


def plant_rows(plants: list[dict[str, Any]]) -> list[Row]:
    """Plants tab rows keyed by plant id; the temperature column shows the ideal range when known."""
    rows: list[Row] = []
    for p in plants:
        temp = (
            f"{fmt.num(p.get('ideal_temp_min'), '', 0)}–{fmt.num(p.get('ideal_temp_max'), '°C', 0)}"
            if p.get("ideal_temp_min") is not None
            else "—"
        )
        rows.append(
            (
                str(p["id"]),
                [
                    str(p["id"]),
                    p["species"],
                    p.get("category") or "—",
                    p.get("water_needs") or "—",
                    p.get("light_needs") or "—",
                    temp,
                ],
            )
        )
    return rows


def sensor_rows(status: dict[str, Any]) -> list[Row]:
    """Sensors tab rows keyed by sensor id, joined to their plant's species and their latest reading."""
    rows: list[Row] = []
    species = {p["id"]: p["species"] for p in status.get("plants", [])}
    for s in status.get("sensors", []):
        r = s.get("last_reading") or {}
        battery = r.get("battery_state") or "—"
        if r.get("water_warning"):
            battery = f"{battery} ⚠ water"
        rows.append(
            (
                str(s["id"]),
                [
                    str(s["id"]),
                    s["name"],
                    s["type"],
                    species.get(s.get("plant_id"), "—"),
                    fmt.num(r.get("soil_moisture"), "%"),
                    fmt.num(r.get("temperature"), "°C"),
                    fmt.num(r.get("env_humidity"), "%", 0),
                    fmt.num(r.get("light"), " lx", 0),
                    battery,
                    fmt.age(s.get("reading_age_seconds")),
                ],
            )
        )
    return rows


def decision_rows(payload: dict[str, Any] | None) -> list[Row]:
    """Decisions tab rows (unkeyed) in the order the API lists the logged evaluations."""
    rows: list[Row] = []
    for d in (payload or {}).get("items", []):
        rows.append(
            (
                None,
                [
                    fmt.clock(d["evaluated_at"], with_date=True),
                    fmt.styled(d["action"], fmt.ACTION_STYLES),
                    str(d["duration_minutes"]),
                    f"{d['interval_hours']}h",
                    f"{d['confidence']:.0%}",
                    d.get("primary_code") or "—",
                    d.get("triggered_by", ""),
                    Text("yes", style="#4fb3ff") if d.get("actuated") else Text("no", style="dim"),
                    d.get("reason_text", ""),
                ],
            )
        )
    return rows


def history_rows(payload: dict[str, Any] | None) -> list[Row]:
    """History tab rows (unkeyed): every irrigator's events merged, newest first."""
    events = [
        (ev, irr["irrigator_name"]) for irr in (payload or {}).get("irrigators", []) for ev in irr.get("events", [])
    ]
    rows: list[Row] = []
    for ev, name in sorted(events, key=lambda r: r[0]["timestamp"], reverse=True):
        rows.append(
            (
                None,
                [
                    fmt.clock(ev["timestamp"], with_date=True),
                    name,
                    fmt.styled(ev["action"], fmt.ACTION_STYLES),
                    fmt.num(ev.get("duration_minutes"), "", 0),
                    ev.get("triggered_by", ""),
                    ev.get("notes") or "",
                ],
            )
        )
    return rows


def config_rows(effective: dict[str, Any] | None) -> list[tuple[str, Text]]:
    """Effective-config rows sorted by key; values the cluster overrides are bold, each tagged with its source."""
    rows: list[tuple[str, Text]] = []
    for key, field in sorted(((effective or {}).get("effective") or {}).items()):
        value = field.get("value")
        source = field.get("source", "")
        style = "bold" if source == "cluster" else ""
        rows.append((key, Text.assemble((str(value), style), (f"  ↳ {source}", "dim"))))
    return rows


def window_rows(windows: list[dict[str, Any]]) -> list[Row]:
    """Windows tab rows keyed by window id: label, hour span and weekday mask."""
    rows: list[Row] = []
    for w in windows:
        rows.append(
            (
                str(w["id"]),
                [
                    str(w["id"]),
                    w.get("label") or "—",
                    f"{w['start_hour']:02d}:00–{w['end_hour']:02d}:00",
                    fmt.weekday_mask(w["weekday_mask"]),
                ],
            )
        )
    return rows


def irrigator_info(summary: ClusterSummary) -> Text:
    """Overview irrigator panel: name, watering state and last event, or the sensor-only hint."""
    s = summary
    info = Text()
    if s.irrigator_id is None:
        info.append("No irrigator\n", style="bold")
        info.append("sensor-only cluster\n", style="dim")
        info.append("n: attach an irrigator", style="dim")
        return info
    info.append(f"{s.irrigator_name}\n", style="bold")
    if s.watering:
        info.append("● watering now\n", style="bold #4fb3ff")
    else:
        info.append("idle\n", style="dim")
    ev = s.last_event
    if ev:
        info.append(f"last {ev['action']} {fmt.ago(ev['timestamp'])}")
        if ev.get("duration_minutes"):
            info.append(f" · {ev['duration_minutes']}m")
        info.append(f"\nby {ev.get('triggered_by', '?')}", style="dim")
    info.append("\nu edit · del detach", style="dim")
    return info


def decision_panel(decision: dict[str, Any]) -> Text:
    """Overview decision panel: action, duration/interval, confidence and the reason trail (``{}`` = none yet)."""
    text = Text.assemble(("Decision engine\n", "bold"))
    if not decision:
        text.append("no decision available", style="dim")
        return text
    text.append_text(fmt.styled(decision.get("action"), fmt.ACTION_STYLES))
    if decision.get("duration_minutes"):
        text.append(f"  {decision['duration_minutes']} min")
    if decision.get("interval_hours"):
        text.append(f" · every {decision['interval_hours']}h")
    text.append(f"  confidence {decision.get('confidence', 0):.0%}\n", style="dim")
    for reason in decision.get("reasons", []):
        sev = reason.get("severity", "info")
        text.append(f"{reason.get('icon') or '•'} ", style=fmt.SEVERITY_STYLES.get(sev, ""))
        text.append(f"{reason.get('message', '')} ")
        text.append(f"[{reason.get('code', '')}]\n", style="dim")
    if not decision.get("reasons"):
        text.append(decision.get("reason", ""))
    return text


def _next_water(forecast: dict[str, Any]) -> str | Text:
    hours = forecast.get("hours_until_next")
    if hours is None:
        return "—"
    if hours <= 0:
        return Text("due now", style="bold #e0c341")
    return f"{fmt.ago(forecast.get('next_predicted_at'))} ({fmt.clock(forecast.get('next_predicted_at'))})"


def forecast_rows(forecast: dict[str, Any]) -> list[tuple[str, str | Text]]:
    """Forecast panel rows: next watering, projected minimum, method, then rain / weather-skip notes when present."""
    f = forecast
    rows: list[tuple[str, str | Text]] = [
        ("next water", _next_water(f)),
        ("projected min", fmt.num(f.get("projected_min_moisture"), "%")),
        ("method", f"{f.get('method', '?')} ({f.get('confidence', 0):.0%})"),
    ]
    if f.get("precipitation_next_6h_mm") is not None:
        rows.append(("rain 6h", fmt.num(f["precipitation_next_6h_mm"], " mm")))
    if f.get("weather_skip"):
        rows.append(("weather", Text(f.get("weather_reason") or "skip — rain expected", style="#4fb3ff")))
    rows.append(("", Text(f.get("explanation", ""), style="dim")))
    return rows
