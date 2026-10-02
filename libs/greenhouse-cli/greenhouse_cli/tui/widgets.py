"""Reusable Textual widgets: animated sprites, cluster cards, charts, heatmap."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import DataTable, Sparkline, Static
from textual_plotext import PlotextPlot

from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui.model import ClusterSummary
from greenhouse_cli.tui.sprites import MOOD_COLORS, MOOD_LABELS, Mood, logo_sprite, plant_sprite

ANIMATION_INTERVAL = 0.7

SpriteFactory = Callable[[int], RenderableType]


class SpriteView(Static):
    """Displays a sprite, re-rendering it every tick so animated frames play."""

    DEFAULT_CSS = """
    SpriteView { width: auto; height: auto; }
    """

    def __init__(self, factory: SpriteFactory, animate: bool = True, **kwargs: Any) -> None:
        super().__init__(factory(0), **kwargs)
        self._factory = factory
        self._frame = 0
        self._animate = animate  # type: ignore[assignment]  # shadows DOMNode._animate (pre-existing, kept)

    def on_mount(self) -> None:
        if self._animate and getattr(self.app, "animations", True):
            self.set_interval(ANIMATION_INTERVAL, self._tick)

    def _tick(self) -> None:
        self._frame += 1
        self.update(self._factory(self._frame))

    def set_factory(self, factory: SpriteFactory) -> None:
        self._factory = factory
        self.update(factory(self._frame))


def plant_factory(category: str | None, mood: Mood, watering: bool = False) -> SpriteFactory:
    return lambda frame: plant_sprite(category, mood, frame, watering)


class Banner(Static):
    """Greenhouse logo + system health pulse."""

    def update_health(self, health: dict[str, Any] | None, server: str) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column()
        table.add_column()
        title = Text.assemble(("greenhouse", "bold #7ed957"), "  ", (server, "dim"))
        if not health:
            info = Text("connecting…", style="dim")
        else:
            info = Text.assemble(
                "status ",
                fmt.styled(health.get("status"), fmt.STATUS_STYLES),
                "   scheduler ",
                ("running", "#7ed957") if health.get("scheduler_running") else ("stopped", "#ff5f5f"),
                "   cloud ",
                ("reachable", "#7ed957") if health.get("cloud_reachable") else ("unreachable", "#ff5f5f"),
                "\nsensors ",
                (f"{health.get('sensors_fresh', 0)} fresh", "#7ed957"),
                " / ",
                (f"{health.get('sensors_stale', 0)} stale", "#e0c341" if health.get("sensors_stale") else "dim"),
                f"   irrigators {health.get('irrigators_total', 0)}",
                "   alerts ",
                (str(health.get("open_alerts", 0)), "bold #ff5f5f" if health.get("open_alerts") else "#7ed957"),
                f"   last sync {fmt.ago(health.get('last_sync_at'))}",
            )
        table.add_row(logo_sprite(), Group(title, info))
        self.update(table)


class ClusterCard(Vertical, can_focus=True):
    """Dashboard tile for one cluster: sprite, readings, decision, sparkline."""

    class Selected(Message):
        def __init__(self, cluster_id: int) -> None:
            super().__init__()
            self.cluster_id = cluster_id

    BINDINGS = [("enter", "select", "Open")]

    def __init__(self, summary: ClusterSummary, **kwargs: Any) -> None:
        super().__init__(id=f"cluster-card-{summary.id}", classes="cluster-card", **kwargs)
        self.summary = summary

    def compose(self) -> ComposeResult:
        with Horizontal(classes="card-body"):
            yield SpriteView(self._sprite_factory(), classes="card-sprite")
            yield Static(self._info(), classes="card-info")
        yield Sparkline(self.summary.sparkline or [0], classes="card-spark")

    def _sprite_factory(self) -> SpriteFactory:
        s = self.summary
        return plant_factory(s.driest.category if s.driest else None, s.mood, s.watering)

    def _info(self) -> RenderableType:
        s = self.summary
        mood = s.mood
        lines = Text()
        lines.append(f"{s.name}\n", style="bold")
        lines.append(f"{s.environment}", style="dim")
        if s.location:
            lines.append(f" · {s.location}", style="dim")
        lines.append("\n")
        lines.append(f"{MOOD_LABELS[mood]}", style=f"bold {MOOD_COLORS[mood]}")
        if s.driest:
            lines.append(f"  {s.driest.species}", style="italic dim")
        lines.append("\n")
        lines.append_text(fmt.bar(s.min_moisture, 16, s.band_min, s.band_max))
        lines.append(f" {fmt.num(s.min_moisture, '%')}\n")
        lines.append(
            f"🌡 {fmt.num(s.temperature, '°C')}  💧 {fmt.num(s.humidity, '%', 0)}  ☀ {fmt.num(s.light, 'lx', 0)}\n"
        )
        if s.watering:
            lines.append("● watering now\n", style="bold #4fb3ff")
        elif s.last_event:
            lines.append(f"last {s.last_event.get('action')} {fmt.ago(s.last_event.get('timestamp'))}\n", style="dim")
        elif s.irrigator_id is None:
            lines.append("sensor-only\n", style="dim")
        else:
            lines.append("never irrigated\n", style="dim")
        decision = s.decision or {}
        if decision:
            lines.append("next: ")
            lines.append_text(fmt.styled(decision.get("action"), fmt.ACTION_STYLES))
            if decision.get("action") == "irrigate" and decision.get("duration_minutes"):
                lines.append(f" {decision['duration_minutes']}m")
        lines.append(f"\nread {fmt.ago(s.newest_reading_at)}", style="dim")
        return lines

    def action_select(self) -> None:
        self.post_message(self.Selected(self.summary.id))

    def on_click(self) -> None:
        self.action_select()


class PlantTile(Vertical):
    """A plant sprite with its species and moisture gauge underneath."""

    def __init__(
        self,
        species: str,
        category: str | None,
        moisture: float | None,
        mood: Mood,
        watering: bool,
        band: tuple[float | None, float | None] = (None, None),
        **kw: Any,
    ) -> None:
        super().__init__(classes="plant-tile", **kw)
        self._args = (species, category, moisture, mood, watering, band)

    def compose(self) -> ComposeResult:
        species, category, moisture, mood, watering, (lo, hi) = self._args
        yield SpriteView(plant_factory(category, mood, watering))
        caption = Text.assemble((species[:18] + "\n", "bold"))
        caption.append(MOOD_LABELS[mood], style=MOOD_COLORS[mood])
        caption.append(f" {fmt.num(moisture, '%')}\n")
        caption.append_text(fmt.bar(moisture, 16, lo, hi))
        yield Static(caption)


SERIES_COLORS = ["green+", "cyan+", "magenta+", "yellow+", "blue+", "red+", "white"]


class MetricChart(PlotextPlot):
    """A plotext line chart fed by a ``chart-data`` / ``health-timeline`` payload."""

    def show_payload(
        self, payload: dict[str, Any] | None, metric: str = "soil_moisture", hours: int = 24, title: str | None = None
    ) -> None:
        """Plot every sensor series, the ideal band and irrigation events.

        X values are hours relative to now so the axis reads naturally
        ("-24 … 0") and tick labels show the wall-clock time.
        """
        plt = self.plt
        plt.clear_figure()
        label, unit = fmt.METRICS.get(metric, (metric, ""))
        plt.title(title or f"{label} ({unit}) — last {hours}h")
        reference = fmt.now()
        plotted = False
        for i, dataset in enumerate((payload or {}).get("datasets", [])):
            points = dataset.get("points") or []
            if not points:
                continue
            xs = [(ts - reference) / 3600 for ts, _ in points]
            ys = [v for _, v in points]
            plt.plot(xs, ys, label=dataset.get("sensor_name", "?"), marker="braille", color=SERIES_COLORS[i % 7])
            plotted = True
        threshold = (payload or {}).get("threshold") or {}
        for edge in ("min", "max"):
            if threshold.get(edge) is not None:
                plt.hline(threshold[edge], "green")
        self._draw_event_lines((payload or {}).get("events", []), reference)
        plt.xlim(-hours, 0)
        if metric in ("soil_moisture", "env_humidity"):
            plt.ylim(0, 100)
        self._set_x_ticks(hours, reference)
        if not plotted:
            plt.title(f"{label} — no readings in the last {hours}h")
        self.refresh()

    def show_overlay(self, payload: dict[str, Any] | None, hours: int) -> None:
        """Plot the normalised soil / humidity / light overlay (shared 0–100 axis)."""
        plt = self.plt
        plt.clear_figure()
        reference = fmt.now()
        colors = {"soil": "green+", "humidity": "cyan+", "light": "yellow+"}
        plotted = False
        for dataset in (payload or {}).get("datasets", []):
            points = dataset.get("points") or []
            if not points:
                continue
            label = dataset["metric"] + (f" (÷{dataset['original_max']:.0f})" if dataset.get("original_max") else "")
            plt.plot(
                [(ts - reference) / 3600 for ts, _ in points],
                [v for _, v in points],
                label=label,
                marker="braille",
                color=colors.get(dataset["metric"], "white"),
            )
            plotted = True
        self._draw_event_lines((payload or {}).get("events", []), reference)
        plt.title(
            f"Soil · humidity · light, normalised — last {hours}h" if plotted else f"No data in the last {hours}h"
        )
        plt.xlim(-hours, 0)
        plt.ylim(0, 100)
        self._set_x_ticks(hours, reference)
        self.refresh()

    def _draw_event_lines(self, events: list[dict[str, Any]], reference: int) -> None:
        """Mark every irrigation ``start`` as a vertical line at its hour offset from ``reference``."""
        for event in events:
            if event.get("action") == "start":
                self.plt.vline((event["timestamp"] - reference) / 3600, "blue")

    def _set_x_ticks(self, hours: int, reference: int) -> None:
        """Five evenly spaced wall-clock labels across the ``-hours … 0`` axis (with dates beyond one day)."""
        ticks = [-hours + hours * i / 4 for i in range(5)]
        self.plt.xticks(ticks, [fmt.clock(reference + t * 3600, with_date=hours > 24) for t in ticks])

    def show_timeline(self, payload: dict[str, Any] | None, title: str) -> None:
        """Plot a ``(timestamp, score)`` timeline such as plant health."""
        plt = self.plt
        plt.clear_figure()
        points = (payload or {}).get("points") or []
        plt.title(title if points else f"{title} — no data yet")
        if points:
            reference = fmt.now()
            xs = [(ts - reference) / 86400 for ts, _ in points]
            plt.plot(xs, [v for _, v in points], marker="braille", color="green+")
            for name, level in ((payload or {}).get("thresholds") or {}).items():
                plt.hline(level, "green" if name == "good" else "yellow")
            plt.xlabel("days ago")
        plt.ylim(0, 100)
        self.refresh()


class Heatmap(Static):
    """7×24 weekday-by-hour irrigation heatmap from ``GET /clusters/{id}/heatmap``."""

    RAMP = ["#1f2a1f", "#1d4d6b", "#2271a8", "#2f95d6", "#4fb3ff", "#9ad7ff"]
    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def show(self, payload: dict[str, Any] | None) -> None:
        cells = {(c["weekday"], c["hour"]): c for c in (payload or {}).get("cells", [])}
        peak = max((c["count"] for c in cells.values()), default=0)
        text = Text()
        text.append(f"Irrigation heatmap — last {(payload or {}).get('days', 30)} days\n", style="bold")
        text.append("     " + "".join(f"{h:<3}" if h % 3 == 0 else "   " for h in range(24)) + "\n", style="dim")
        for day in range(7):
            text.append(f"{self.DAYS[day]}  ", style="dim")
            for hour in range(24):
                cell = cells.get((day, hour))
                if not cell or not peak:
                    text.append("·· ", style="#333333")
                    continue
                idx = 1 + round((cell["count"] / peak) * (len(self.RAMP) - 2))
                text.append("██", style=self.RAMP[idx])
                text.append(" ")
            text.append("\n")
        text.append(f"\npeak {peak} events/slot", style="dim")
        self.update(text)


class KeyValue(Static):
    """A two-column key/value panel."""

    def show(self, rows: Sequence[tuple[str, RenderableType | str]], title: str | None = None) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim", no_wrap=True)
        table.add_column()
        for key, value in rows:
            table.add_row(key, value)
        if title:
            self.update(Group(Text(title, style="bold"), table))
        else:
            self.update(table)


def selected_key(table: DataTable[Any]) -> str | None:
    """Row key under the cursor, or ``None`` for an empty table."""
    if not table.row_count:
        return None
    row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
    return row_key.value


def refill(table: DataTable[Any], rows: Iterable[tuple[str | None, Sequence[RenderableType | str]]]) -> None:
    """Replace a table's rows while keeping the cursor on the same record.

    Auto-refresh reloads every table; without this the cursor would snap back
    to row 0 and a following edit / delete would target the wrong record.

    Args:
        table: The table to repopulate.
        rows: ``(key, cells)`` pairs; keys must be unique (``None`` = unkeyed).
    """
    previous_key = selected_key(table)
    previous_row = table.cursor_row
    table.clear()
    for key, cells in rows:
        table.add_row(*cells, key=key)
    if not table.row_count:
        return
    try:
        target = table.get_row_index(previous_key) if previous_key is not None else previous_row
    except Exception:  # the record went away — stay at the same position
        target = previous_row
    table.move_cursor(row=min(max(target, 0), table.row_count - 1))
