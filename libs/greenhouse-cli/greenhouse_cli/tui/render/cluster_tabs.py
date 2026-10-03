"""Cluster screen tab tables: plants, sensors, decisions, history, effective config, windows."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui.render._rows import Row


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
    rows: list[Row] = [
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
        for d in (payload or {}).get("items", [])
    ]
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
    """Effective-config rows in repository field order (as the form); cluster overrides bold, tagged by source."""
    rows: list[tuple[str, Text]] = []
    for key, field in ((effective or {}).get("effective") or {}).items():
        value = field.get("value")
        source = field.get("source", "")
        style = "bold" if source == "cluster" else ""
        rows.append((key, Text.assemble((str(value), style), (f"  ↳ {source}", "dim"))))
    return rows


def window_rows(windows: list[dict[str, Any]]) -> list[Row]:
    """Windows tab rows keyed by window id: label, hour span and weekday mask."""
    rows: list[Row] = [
        (
            str(w["id"]),
            [
                str(w["id"]),
                w.get("label") or "—",
                f"{w['start_hour']:02d}:00–{w['end_hour']:02d}:00",
                fmt.weekday_mask(w["weekday_mask"]),
            ],
        )
        for w in windows
    ]
    return rows
