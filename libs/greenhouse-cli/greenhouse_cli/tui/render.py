"""Pure TUI builders: API payload → table rows, key/value rows, rich text (no widgets, no I/O; golden-pinned)."""

from __future__ import annotations

from collections.abc import Iterable
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
    """Effective-config rows in server order (the repository's config field order, as the edit form);
    values the cluster overrides are bold, each tagged with its source."""
    rows: list[tuple[str, Text]] = []
    for key, field in ((effective or {}).get("effective") or {}).items():
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


def insights_text(insights: dict[str, Any] | None, monitor: dict[str, Any] | None) -> Text:
    """Insights panel: each care insight with its suggestion, then who needs water now."""
    text = Text.assemble(("Care insights\n", "bold"))
    for item in (insights or {}).get("insights", []):
        text.append("● ", style=fmt.SEVERITY_STYLES.get(item.get("severity", "info"), ""))
        text.append(f"{item['title']}\n", style="bold")
        text.append(f"  {item['message']}\n")
        if item.get("suggestion"):
            text.append(f"  → {item['suggestion']}\n", style="#7ed957")
    if not (insights or {}).get("insights"):
        text.append("nothing to flag\n", style="dim")
    needs = (monitor or {}).get("needs_water") or []
    text.append("\nNeeds water: ", style="bold")
    text.append(", ".join(needs) if needs else "nobody", style="#e0c341" if needs else "dim")
    return text


def stats_rows(stats: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Stats panel rows: event count, total/average minutes, frequency, and breakdowns by type and trigger."""
    s = stats or {}
    by_type = ", ".join(f"{k} {v}" for k, v in (s.get("events_by_type") or {}).items()) or "—"
    by_trigger = ", ".join(f"{k} {v}" for k, v in (s.get("events_by_trigger") or {}).items()) or "—"
    return [
        ("events", str(s.get("total_events", "—"))),
        ("total", fmt.num(s.get("total_duration_minutes"), " min", 0)),
        ("average", fmt.num(s.get("avg_duration_minutes"), " min")),
        ("per day", fmt.num(s.get("frequency_per_day"), "", 2)),
        ("by type", by_type),
        ("by trigger", by_trigger),
    ]


def efficacy_rows(payload: dict[str, Any] | None) -> list[Row]:
    """Efficacy table rows (unkeyed): moisture before/after each watering and its score (green from 0.5)."""
    rows: list[Row] = []
    for e in (payload or {}).get("items", []):
        score = e.get("score")
        rows.append(
            (
                None,
                [
                    fmt.clock(e["timestamp"], with_date=True),
                    e["irrigator_name"],
                    str(e["duration_minutes"]),
                    fmt.num(e.get("before_pct"), "%"),
                    fmt.num(e.get("after_pct"), "%"),
                    Text(fmt.num(score, "", 2), style="#7ed957" if (score or 0) >= 0.5 else "#e0c341"),
                ],
            )
        )
    return rows


def learn_report(learn: dict[str, Any] | None) -> Text:
    """Learning panel: the server's report text (it already lists its alerts), or a dim placeholder."""
    return Text.assemble(
        ("Learning report\n", "bold"),
        ((learn or {}).get("report") or "no report available", "" if learn else "dim"),
    )


def scheduler_panel_rows(paused: bool | None, health: dict[str, Any] | None) -> list[tuple[str, str | Text]]:
    """System scheduler panel: automatic-run state, scheduler liveness, last sync and the key hints."""
    return [
        (
            "automatic runs",
            Text("paused", style="bold #e0c341") if paused else Text("active", style="bold #7ed957"),
        ),
        ("scheduler", "running" if (health or {}).get("scheduler_running") else "stopped"),
        ("last sync", fmt.ago((health or {}).get("last_sync_at"))),
        ("", Text("p pause/resume · S sync · P plant DB · H health snapshot · del remove job", style="dim")),
    ]


def job_rows(jobs: Iterable[dict[str, Any]]) -> list[Row]:
    """Scheduler job rows keyed by job id; built-in jobs are tagged."""
    rows: list[Row] = []
    for job in jobs:
        rows.append(
            (
                job["id"],
                [
                    Text.assemble(job["name"], (" · built-in", "dim") if job.get("core") else ""),
                    job["trigger"],
                    job.get("next_run_time") or "—",
                    Text("paused", style="#e0c341") if job.get("paused") else Text("active", style="#7ed957"),
                ],
            )
        )
    return rows


def device_rows(health: dict[str, Any] | None) -> list[Row]:
    """Device freshness rows (unkeyed) from the system-health payload."""
    rows: list[Row] = []
    for d in (health or {}).get("devices", []):
        rows.append(
            (
                None,
                [
                    str(d["id"]),
                    d["name"],
                    fmt.styled(d["status"], fmt.STATUS_STYLES),
                    fmt.age(d.get("age_seconds")),
                    d.get("note") or "",
                ],
            )
        )
    return rows


def quality_rows(issues: list[dict[str, Any]]) -> list[Row]:
    """Data-quality issue rows (unkeyed); the entity column carries the id when there is one."""
    rows: list[Row] = []
    for issue in issues:
        entity = issue["entity_type"] + (f" #{issue['entity_id']}" if issue.get("entity_id") is not None else "")
        rows.append(
            (
                None,
                [
                    fmt.styled(issue["severity"], fmt.SEVERITY_STYLES),
                    issue["code"],
                    entity,
                    issue["label"],
                    issue["message"],
                ],
            )
        )
    return rows


def account_line(who: str | None, server_url: str) -> str:
    """Settings account line (markup): who is signed in where, or that auth is off / nobody is signed in."""
    return (
        f"Signed in as [b]{who}[/b] on {server_url}   [dim]O: log out[/dim]"
        if who
        else f"Server {server_url}  [dim](auth disabled or not signed in)[/dim]"
    )


def preference_rows(prefs: dict[str, Any]) -> list[tuple[str, str]]:
    """Preference rows sorted by key, or a single "unavailable" row."""
    return [(k, str(v)) for k, v in sorted(prefs.items())] or [("preferences", "unavailable")]


def global_config_rows(config: dict[str, Any]) -> list[tuple[str, str | Text]]:
    """Global-default rows in server order — the repository's config field order, as the edit form
    (bookkeeping fields hidden; unset = built-in default), or "unavailable"."""
    return [
        (k, Text("built-in default", style="dim") if v is None else str(v))
        for k, v in config.items()
        if k not in {"id", "last_updated"}
    ] or [("config", "unavailable")]


def vacation_rows(vacations: list[dict[str, Any]], active_id: int | None) -> list[Row]:
    """Vacation rows keyed by window id, with an active / past / upcoming state column."""
    rows: list[Row] = []
    now = fmt.now()
    for v in vacations:
        if v["id"] == active_id:
            state = Text("active", style="bold #7ed957")
        elif v["ends_at"] < now:
            state = Text("past", style="dim")
        else:
            state = Text(f"starts {fmt.ago(v['starts_at'])}", style="#6fb7ff")
        rows.append(
            (
                str(v["id"]),
                [
                    str(v["id"]),
                    fmt.clock(v["starts_at"], True),
                    fmt.clock(v["ends_at"], True),
                    v.get("contact_email") or "—",
                    v.get("notes") or "",
                    state,
                ],
            )
        )
    return rows
