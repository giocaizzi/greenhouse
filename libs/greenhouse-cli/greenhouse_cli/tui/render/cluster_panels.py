"""Cluster screen panels: overview (irrigator, decision, forecast) and insights (care, stats, efficacy, learning)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text

from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui.render._rows import Row

if TYPE_CHECKING:
    from greenhouse_cli.tui.model import ClusterSummary

_GOOD_EFFICACY_SCORE = 0.5  # efficacy scores from here up render green


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
    """Next-watering cell: ``—`` without a prediction, a highlighted *due now* when overdue, else relative + clock."""
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
                    Text(fmt.num(score, "", 2), style="#7ed957" if (score or 0) >= _GOOD_EFFICACY_SCORE else "#e0c341"),
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
