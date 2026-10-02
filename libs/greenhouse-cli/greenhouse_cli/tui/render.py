"""Pure TUI builders: API payload → table rows, key/value rows, rich text (no widgets, no I/O; golden-pinned)."""

from __future__ import annotations

from typing import Any

from rich.console import RenderableType

from greenhouse_cli.tui import formatting as fmt

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
