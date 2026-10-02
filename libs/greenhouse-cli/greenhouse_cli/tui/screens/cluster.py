"""Cluster detail: plants garden, decision trail, charts, CRUD tables and actions.

Keys are contextual on the active tab: ``n`` adds, ``u`` edits the selected
row and ``delete`` removes it (plants, sensors, windows; on Overview they act
on the irrigator; on Config ``u`` edits the cluster's irrigation config).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static, TabbedContent, TabPane

from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui import render, resources
from greenhouse_cli.tui.model import ClusterSummary, summarize
from greenhouse_cli.tui.screens.base import DataScreen
from greenhouse_cli.tui.screens.forms import Field
from greenhouse_cli.tui.screens.modals import ConfirmScreen, IrrigateScreen, WaterNowScreen
from greenhouse_cli.tui.sprites import watering_can_sprite
from greenhouse_cli.tui.widgets import Heatmap, KeyValue, MetricChart, PlantTile, SpriteView, refill, selected_key

if TYPE_CHECKING:
    from rich.console import RenderableType

METRIC_ORDER = ["soil_moisture", "temperature", "env_humidity", "light", "overlay"]
RANGES = [6, 24, 72, 168, 720]
STATS_DAYS = 7


def _next_water(forecast: dict[str, Any]) -> str | Text:
    hours = forecast.get("hours_until_next")
    if hours is None:
        return "—"
    if hours <= 0:
        return Text("due now", style="bold #e0c341")
    return f"{fmt.ago(forecast.get('next_predicted_at'))} ({fmt.clock(forecast.get('next_predicted_at'))})"


class ClusterScreen(DataScreen):
    """Everything about one cluster, in tabs."""

    AUTO_REFRESH = True
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("i", "irrigate", "Irrigate"),
        Binding("w", "water_now", "Water now"),
        Binding("x", "stop", "Stop"),
        Binding("c", "check", "Check"),
        Binding("n", "new", "New"),
        Binding("u", "edit", "Edit"),
        Binding("delete", "delete", "Delete"),
        Binding("m", "cycle_metric", "Metric", show=False),
        Binding("[", "range(-1)", "Range -", show=False),
        Binding("]", "range(1)", "Range +", show=False),
        Binding("L", "log_manual", "Log manual", show=False),
        Binding("M", "move_plant", "Move plant", show=False),
        Binding("P", "plant_sync", "Plant-DB sync", show=False),
        Binding("e", "edit_cluster", "Edit cluster", show=False),
        Binding("D", "delete_cluster", "Delete cluster", show=False),
        Binding("E", "export_stats", "Export CSV", show=False),
    ]

    def __init__(self, cluster_id: int) -> None:
        super().__init__()
        self.cluster_id = cluster_id
        self.summary: ClusterSummary | None = None
        self.status: dict[str, Any] = {}
        self.detail: dict[str, Any] = {}
        self.metric = "soil_moisture"
        self.hours = 24
        self._plants_loaded: list[int] = []
        self.plant_chart = "health"
        self._plant_id: int | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="cluster-tabs"):
            with TabPane("Overview", id="tab-overview"), VerticalScroll():
                yield HorizontalScroll(id="garden")
                with Horizontal(id="overview-panels"):
                    with Vertical(id="irrigator-panel", classes="panel"):
                        yield SpriteView(lambda f: watering_can_sprite(False, f), id="can")
                        yield Static(id="irrigator-info")
                    yield Static(id="decision-panel", classes="panel")
                    yield KeyValue(id="forecast-panel", classes="panel")
            with TabPane("Charts", id="tab-charts"), Vertical():
                yield Static(id="chart-hint", classes="hint")
                yield MetricChart(id="metric-chart")
                yield Heatmap(id="heatmap")
            with TabPane("Plants", id="tab-plants"), Vertical():
                yield Static(
                    "[dim]n add · u edit · del delete · M move to cluster · P refresh care data from plant DB · "
                    "m health ↔ moisture chart[/dim]",
                    classes="hint",
                )
                yield DataTable(id="plants-table", cursor_type="row", zebra_stripes=True)
                yield MetricChart(id="plant-chart")
            with TabPane("Sensors", id="tab-sensors"), Vertical():
                yield Static("[dim]n add · u edit · del delete[/dim]", classes="hint")
                yield DataTable(id="sensors-table", cursor_type="row", zebra_stripes=True)
            with TabPane("Insights", id="tab-insights"), VerticalScroll():
                yield Static("[dim]E export stats CSV · learning report runs on open[/dim]", classes="hint")
                with Horizontal(id="insights-row"):
                    yield Static(id="insights-panel", classes="panel")
                    yield KeyValue(id="stats-panel", classes="panel")
                yield Static("[b]Irrigation efficacy (14 days)[/b]", classes="hint")
                yield DataTable(id="efficacy-table", cursor_type="row", zebra_stripes=True)
                yield Static(id="learn-panel", classes="panel")
            with TabPane("Decisions", id="tab-decisions"):
                yield DataTable(id="decisions-table", cursor_type="row", zebra_stripes=True)
            with TabPane("History", id="tab-history"), Vertical():
                yield Static("[dim]L log a manual watering[/dim]", classes="hint")
                yield DataTable(id="history-table", cursor_type="row", zebra_stripes=True)
            with TabPane("Windows", id="tab-windows"), Vertical():
                yield Static(
                    "[dim]Irrigation windows gate automatic runs. None = all hours allowed (subject to quiet "
                    "hours).  n add · u edit · del delete[/dim]",
                    classes="hint",
                )
                yield DataTable(id="windows-table", cursor_type="row", zebra_stripes=True)
            with TabPane("Config", id="tab-config"), VerticalScroll():
                yield Static("[dim]u edit config · e edit cluster · D delete cluster[/dim]", classes="hint")
                yield KeyValue(id="config-panel", classes="panel")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#plants-table", DataTable).add_columns("ID", "Species", "Category", "Water", "Light", "Temp")
        self.query_one("#sensors-table", DataTable).add_columns(
            "ID", "Name", "Type", "Plant", "Soil", "Temp", "Humidity", "Light", "Battery", "Age"
        )
        self.query_one("#decisions-table", DataTable).add_columns(
            "When", "Action", "Min", "Every", "Conf.", "Code", "Trigger", "Acted", "Reason"
        )
        self.query_one("#history-table", DataTable).add_columns("When", "Irrigator", "Action", "Minutes", "By", "Notes")
        self.query_one("#windows-table", DataTable).add_columns("ID", "Label", "Hours", "Days")
        self.query_one("#efficacy-table", DataTable).add_columns("When", "Irrigator", "Min", "Before", "After", "Score")

    @property
    def active_tab(self) -> str:
        return self.query_one(TabbedContent).active

    # ── Loading ──────────────────────────────────────────────────────────

    async def load(self) -> None:
        cid = self.cluster_id
        api = self.gh.api
        status, soil = await asyncio.gather(
            api(lambda c: c.status(cid)),
            api(lambda c: c.cluster_chart_data(cid, hours=24), quiet=True),
        )
        if status is None:
            return
        self.status = status
        self.summary = summarize(status, soil)
        self.title = f"greenhouse · {self.summary.name}"
        await self._render_overview(status)
        self._render_sensors(status)
        self._render_plants(status)
        tasks = [
            self._load_forecast(),
            self._load_chart(soil if self.metric == "soil_moisture" and self.hours == 24 else None),
            self._load_heatmap(),
            self._load_decisions(),
            self._load_history(),
            self._load_config(),
        ]
        if self.active_tab == "tab-insights":
            tasks.append(self._load_insights())
        await asyncio.gather(*tasks)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "tab-insights":
            self.run_worker(self._load_insights(), group="insights", exclusive=True)

    async def _render_overview(self, status: dict[str, Any]) -> None:
        s = self.summary
        assert s is not None
        garden = self.query_one("#garden", HorizontalScroll)
        await garden.remove_children()
        if s.plants:
            await garden.mount_all(
                PlantTile(
                    p.species,
                    p.category,
                    p.moisture,
                    p.mood,
                    s.watering,
                    (s.band_min, s.band_max),
                    id=f"plant-tile-{p.id}",
                )
                for p in s.plants
            )
        else:
            await garden.mount(
                Static("No plants in this cluster yet — open the Plants tab and press n.", classes="hint")
            )

        self.query_one("#can", SpriteView).set_factory(lambda f: watering_can_sprite(s.watering, f))
        info = Text()
        if s.irrigator_id is None:
            info.append("No irrigator\n", style="bold")
            info.append("sensor-only cluster\n", style="dim")
            info.append("n: attach an irrigator", style="dim")
        else:
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
        self.query_one("#irrigator-info", Static).update(info)

        decision = status.get("decision") or {}
        text = Text.assemble(("Decision engine\n", "bold"))
        if not decision:
            text.append("no decision available", style="dim")
        else:
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
        self.query_one("#decision-panel", Static).update(text)

    async def _load_forecast(self) -> None:
        f = await self.gh.api(lambda c: c.forecast(self.cluster_id), quiet=True)
        panel = self.query_one("#forecast-panel", KeyValue)
        if not f:
            panel.show([("forecast", "unavailable")], title="Forecast")
            return
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
        panel.show(rows, title="Forecast")

    async def _load_chart(self, payload: dict[str, Any] | None = None) -> None:
        label = "Overlay: soil / humidity / light (0-100)" if self.metric == "overlay" else fmt.METRICS[self.metric][0]
        self.query_one("#chart-hint", Static).update(
            f"[b]{label}[/b] · {self.hours}h   [dim]m: next metric   [ / ]: shorter / longer range[/dim]"
        )
        metric, hours, cid = self.metric, self.hours, self.cluster_id
        chart = self.query_one("#metric-chart", MetricChart)
        if metric == "overlay":
            chart.show_overlay(await self.gh.api(lambda c: c.cluster_overlay(cid, hours=hours)), hours)
            return
        if payload is None:
            payload = await self.gh.api(lambda c: c.cluster_chart_data(cid, hours=hours, metric=metric))
        chart.show_payload(payload, metric, hours)

    async def _load_heatmap(self) -> None:
        payload = await self.gh.api(lambda c: c.cluster_heatmap(self.cluster_id), quiet=True)
        self.query_one("#heatmap", Heatmap).show(payload)

    def _render_plants(self, status: dict[str, Any]) -> None:
        table = self.query_one("#plants-table", DataTable)
        refill(table, render.plant_rows(status.get("plants", [])))
        ids = [p["id"] for p in status.get("plants", [])]
        if ids and ids != self._plants_loaded:
            self._plants_loaded = ids
            self.run_worker(self._load_plant_health(ids[0]), group="plant-health", exclusive=True)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "plants-table" and event.row_key.value:
            self.run_worker(self._load_plant_health(int(event.row_key.value)), group="plant-health", exclusive=True)

    async def _load_plant_health(self, plant_id: int) -> None:
        self._plant_id = plant_id
        species = next((p["species"] for p in self.status.get("plants", []) if p["id"] == plant_id), "")
        chart = self.query_one("#plant-chart", MetricChart)
        if self.plant_chart == "moisture":
            payload = await self.gh.api(lambda c: c.plant_chart_data(plant_id, hours=72), quiet=True)
            chart.show_payload(payload, "soil_moisture", 72, title=f"Soil moisture — {species} (last 72h)")
            return
        timeline, health = await asyncio.gather(
            self.gh.api(lambda c: c.plant_health_timeline(plant_id), quiet=True),
            self.gh.api(lambda c: c.plant_health(plant_id), quiet=True),
        )
        score = (health or {}).get("current_score")
        title = f"Health — {species}" + (f" · today {score:.0f}/100" if score is not None else "") + " (90 days)"
        chart.show_timeline(timeline, title)

    def _render_sensors(self, status: dict[str, Any]) -> None:
        table = self.query_one("#sensors-table", DataTable)
        refill(table, render.sensor_rows(status))

    async def _load_decisions(self) -> None:
        data = await self.gh.api(lambda c: c.list_decisions(self.cluster_id, limit=100), quiet=True)
        table = self.query_one("#decisions-table", DataTable)
        rows: list[tuple[str | None, list[RenderableType | str]]] = []
        for d in (data or {}).get("items", []):
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
        refill(table, rows)

    async def _load_history(self) -> None:
        data = await self.gh.api(lambda c: c.history(self.cluster_id, hours=24 * 30, limit=200), quiet=True)
        table = self.query_one("#history-table", DataTable)
        events = [
            (ev, irr["irrigator_name"]) for irr in (data or {}).get("irrigators", []) for ev in irr.get("events", [])
        ]
        rows: list[tuple[str | None, list[RenderableType | str]]] = []
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
        refill(table, rows)

    async def _load_config(self) -> None:
        cid = self.cluster_id
        effective, detail = await asyncio.gather(
            self.gh.api(lambda c: c.get_effective_config(cid), quiet=True),
            self.gh.api(lambda c: c.get_cluster_detail(cid), quiet=True),
        )
        self.detail = detail or {}
        rows: list[tuple[str, str | Text]] = []
        for key, field in sorted(((effective or {}).get("effective") or {}).items()):
            value = field.get("value")
            source = field.get("source", "")
            style = "bold" if source == "cluster" else ""
            rows.append((key, Text.assemble((str(value), style), (f"  ↳ {source}", "dim"))))
        self.query_one("#config-panel", KeyValue).show(rows or [("config", "unavailable")], title="Effective config")
        table = self.query_one("#windows-table", DataTable)
        window_rows: list[tuple[str | None, list[RenderableType | str]]] = []
        for w in self.detail.get("windows", []):
            window_rows.append(
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
        refill(table, window_rows)

    async def _load_insights(self) -> None:
        cid = self.cluster_id
        api = self.gh.api
        insights, monitor, stats, efficacy, learn = await asyncio.gather(
            api(lambda c: c.insights(cid), quiet=True),
            api(lambda c: c.monitor(cid), quiet=True),
            api(lambda c: c.stats(cid, days=STATS_DAYS), quiet=True),
            api(lambda c: c.efficacy(cid), quiet=True),
            api(lambda c: c.learn(cid), quiet=True),
        )
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
        self.query_one("#insights-panel", Static).update(text)

        s = stats or {}
        by_type = ", ".join(f"{k} {v}" for k, v in (s.get("events_by_type") or {}).items()) or "—"
        by_trigger = ", ".join(f"{k} {v}" for k, v in (s.get("events_by_trigger") or {}).items()) or "—"
        self.query_one("#stats-panel", KeyValue).show(
            [
                ("events", str(s.get("total_events", "—"))),
                ("total", fmt.num(s.get("total_duration_minutes"), " min", 0)),
                ("average", fmt.num(s.get("avg_duration_minutes"), " min")),
                ("per day", fmt.num(s.get("frequency_per_day"), "", 2)),
                ("by type", by_type),
                ("by trigger", by_trigger),
            ],
            title=f"Stats — last {STATS_DAYS} days",
        )

        table = self.query_one("#efficacy-table", DataTable)
        rows: list[tuple[str | None, list[RenderableType | str]]] = []
        for e in (efficacy or {}).get("items", []):
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
        refill(table, rows)

        report = Text.assemble(
            ("Learning report\n", "bold"),
            ((learn or {}).get("report") or "no report available", "" if learn else "dim"),
        )
        self.query_one("#learn-panel", Static).update(report)  # the report already lists its alerts

    # ── Charts ───────────────────────────────────────────────────────────

    def action_cycle_metric(self) -> None:
        if self.active_tab == "tab-plants":
            self.plant_chart = "moisture" if self.plant_chart == "health" else "health"
            if self._plant_id is not None:
                self.run_worker(self._load_plant_health(self._plant_id), group="plant-health", exclusive=True)
            return
        self.metric = METRIC_ORDER[(METRIC_ORDER.index(self.metric) + 1) % len(METRIC_ORDER)]
        self.run_worker(self._load_chart(), group="chart", exclusive=True)

    def action_range(self, step: int) -> None:
        idx = max(0, min(len(RANGES) - 1, RANGES.index(self.hours) + step))
        self.hours = RANGES[idx]
        self.run_worker(self._load_chart(), group="chart", exclusive=True)

    # ── Irrigation actions ───────────────────────────────────────────────

    def action_irrigate(self) -> None:
        name = self.summary.name if self.summary else f"cluster {self.cluster_id}"

        def _after(opts: dict[str, Any] | None) -> None:
            if opts is None:
                return
            self.run_worker(
                self.act(
                    lambda c: c.irrigate(self.cluster_id, **opts),
                    lambda r: f"{'Dry run' if opts['dry_run'] else 'Irrigate'}: {r.get('action')} — {r.get('reason')}",
                ),
                group="act",
            )

        self.app.push_screen(IrrigateScreen(name), _after)

    def _irrigator(self) -> tuple[int, str] | None:
        if not self.summary or self.summary.irrigator_id is None:
            self.notify("This cluster has no irrigator.", severity="warning")
            return None
        return self.summary.irrigator_id, self.summary.irrigator_name or "irrigator"

    def action_water_now(self) -> None:
        irrigator = self._irrigator()
        if not irrigator:
            return
        irrigator_id, name = irrigator

        def _after(minutes: int | None) -> None:
            if minutes is None:
                return
            self.run_worker(
                self.act(lambda c: c.start_irrigator(irrigator_id, minutes or None), lambda r: r.get("message", "")),
                group="act",
            )

        self.app.push_screen(WaterNowScreen(name), _after)

    def action_stop(self) -> None:
        irrigator = self._irrigator()
        if not irrigator:
            return
        irrigator_id, name = irrigator
        self.confirm_then(
            f"Stop [b]{name}[/b] now?", lambda c: c.stop_irrigator(irrigator_id), lambda r: r.get("message", ""), "Stop"
        )

    def action_check(self) -> None:
        self.confirm_then(
            "Run the scheduled check for this cluster now?\nIf auto-run is enabled it may irrigate.",
            lambda c: c.check(self.cluster_id),
            lambda r: f"Check: {r.get('action')}" + (f" — {r['notes']}" if r.get("notes") else ""),
            "Check",
        )

    def action_log_manual(self) -> None:
        irrigator = self._irrigator()
        if not irrigator:
            return
        irrigator_id, name = irrigator
        self.form_then(
            f"Log a manual watering — {name}",
            [Field("minutes", "Minutes", "int", required=True), Field("notes", "Notes")],
            lambda v: lambda c: c.log_manual(irrigator_id, v["minutes"], v["notes"]),
            "Manual watering logged",
            "Log",
            note="Records an event you did by hand (feeds cooldown + learning). Nothing is actuated.",
        )

    # ── CRUD (contextual on the active tab) ──────────────────────────────

    def action_new(self) -> None:
        cid = self.cluster_id
        tab = self.active_tab
        if tab == "tab-plants":
            self.form_then(
                "Add plant",
                resources.plant_fields(),
                lambda v: lambda c: c.add_plant(cid, **v),
                lambda r: f"Added {r.get('species')}",
                "Add",
                note="Blank care fields are filled from the plant DB when the species is known.",
            )
        elif tab == "tab-sensors":
            self.form_then(
                "Add sensor",
                resources.sensor_fields(None, self.status.get("plants", [])),
                lambda v: lambda c: c.add_sensor(cid, **v),
                lambda r: f"Added sensor {r.get('name')}",
                "Add",
            )
        elif tab == "tab-windows":
            self.form_then(
                "Add irrigation window",
                resources.window_fields(),
                lambda v: (
                    lambda c: c.add_window(cid, v["start_hour"], v["end_hour"], v["weekday_mask"] or 127, v["label"])
                ),
                "Window added",
                "Add",
            )
        elif tab == "tab-overview":
            if self.summary and self.summary.irrigator_id is not None:
                self.notify("This cluster already has an irrigator — press u to edit it.", severity="warning")
                return
            self.form_then(
                "Attach irrigator",
                resources.irrigator_fields(),
                lambda v: lambda c: c.add_irrigator(cid, **v),
                lambda r: f"Attached {r.get('name')}",
                "Attach",
            )
        else:
            self.notify("Nothing to add here — try the Plants, Sensors, Windows or Overview tab.")

    def action_edit(self) -> None:
        cid = self.cluster_id
        tab = self.active_tab
        if tab == "tab-plants":
            plant = self._selected("#plants-table", self.status.get("plants", []))
            if plant:
                self.form_then(
                    f"Edit plant #{plant['id']}",
                    resources.plant_fields(plant),
                    lambda v: lambda c: c.update_plant(cid, plant["id"], **v),
                    "Plant updated",
                )
        elif tab == "tab-sensors":
            sensor = self._selected("#sensors-table", self.detail.get("sensors", []))
            if sensor:
                self.form_then(
                    f"Edit sensor #{sensor['id']}",
                    resources.sensor_fields(sensor, self.status.get("plants", [])),
                    lambda v: lambda c: c.update_sensor(cid, sensor["id"], **v),
                    "Sensor updated",
                )
        elif tab == "tab-windows":
            window = self._selected("#windows-table", self.detail.get("windows", []))
            if window:
                self.form_then(
                    f"Edit window #{window['id']}",
                    resources.window_fields(window),
                    lambda v: lambda c: c.update_window(cid, window["id"], **v),
                    "Window updated",
                )
        elif tab == "tab-overview":
            irrigator = self.detail.get("irrigator")
            if not irrigator:
                self.notify("No irrigator — press n to attach one.", severity="warning")
                return
            self.form_then(
                f"Edit irrigator — {irrigator['name']}",
                resources.irrigator_fields(irrigator),
                lambda v: lambda c: c.update_irrigator(cid, **v),
                "Irrigator updated",
            )
        elif tab == "tab-config":
            self.form_then(
                "Edit irrigation config",
                resources.config_fields(self.detail.get("config")),
                lambda v: lambda c: c.set_config(cid, **v),
                "Config saved",
                note="Blank = keep the current value. Global defaults live on the Settings screen (o).",
            )
        else:
            self.notify("Nothing to edit here — try Plants, Sensors, Windows, Overview or Config.")

    def action_delete(self) -> None:
        cid = self.cluster_id
        tab = self.active_tab
        if tab == "tab-plants":
            plant = self._selected("#plants-table", self.status.get("plants", []))
            if plant:
                self.confirm_then(
                    f"Delete plant [b]{plant['species']}[/b]?",
                    lambda c: c.delete_plant(cid, plant["id"]),
                    "Plant deleted",
                    "Delete",
                )
        elif tab == "tab-sensors":
            sensor = self._selected("#sensors-table", self.detail.get("sensors", []))
            if sensor:
                self.confirm_then(
                    f"Delete sensor [b]{sensor['name']}[/b]? Its readings are kept.",
                    lambda c: c.delete_sensor(cid, sensor["id"]),
                    "Sensor deleted",
                    "Delete",
                )
        elif tab == "tab-windows":
            window = self._selected("#windows-table", self.detail.get("windows", []))
            if window:
                self.confirm_then(
                    f"Delete window {window['start_hour']:02d}–{window['end_hour']:02d}h?",
                    lambda c: c.delete_window(cid, window["id"]),
                    "Window deleted",
                    "Delete",
                )
        elif tab == "tab-overview":
            irrigator = self._irrigator()
            if irrigator:
                self.confirm_then(
                    f"Detach irrigator [b]{irrigator[1]}[/b] from this cluster?",
                    lambda c: c.delete_irrigator(cid),
                    "Irrigator detached",
                    "Detach",
                )
        else:
            self.notify("Nothing to delete here — use D to delete the whole cluster.")

    def _selected(self, table_id: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        key = selected_key(self.query_one(table_id, DataTable))
        row = next((r for r in rows if str(r["id"]) == key), None)
        if row is None:
            self.notify("Select a row first.", severity="warning")
        return row

    def action_move_plant(self) -> None:
        plant = self._selected("#plants-table", self.status.get("plants", []))
        if not plant:
            return
        self.run_worker(self._move_plant(plant), group="act")

    async def _move_plant(self, plant: dict[str, Any]) -> None:
        clusters = await self.gh.api(lambda c: c.list_clusters())
        options = [(f"{c['name']} (#{c['id']})", c["id"]) for c in clusters or [] if c["id"] != self.cluster_id]
        if not options:
            self.notify("No other cluster to move to.", severity="warning")
            return
        self.form_then(
            f"Move {plant['species']}",
            [Field("target_cluster_id", "Target cluster", "select", None, options, required=True)],
            lambda v: lambda c: c.move_plant(plant["id"], v["target_cluster_id"]),
            "Plant moved",
            "Move",
        )

    def action_plant_sync(self) -> None:
        self.run_worker(
            self.act(
                lambda c: c.sync_plants(cluster_id=self.cluster_id),
                lambda r: f"Plant DB sync: {r.get('synced', 0)} updated, {len(r.get('errors', []))} errors",
            ),
            group="act",
        )

    def action_edit_cluster(self) -> None:
        cluster = self.status.get("cluster")
        if cluster:
            self.form_then(
                "Edit cluster",
                resources.cluster_fields(cluster),
                lambda v: lambda c: c.update_cluster(self.cluster_id, **v),
                "Cluster updated",
            )

    def action_delete_cluster(self) -> None:
        name = self.summary.name if self.summary else f"#{self.cluster_id}"

        def _after(ok: bool | None) -> None:
            if ok:
                self.run_worker(self._delete_cluster(), group="act")

        self.app.push_screen(
            ConfirmScreen(
                f"[b red]Delete cluster {name}[/b red] with all its plants, sensors and irrigator?", "Delete"
            ),
            _after,
        )

    async def _delete_cluster(self) -> None:
        if await self.gh.api(lambda c: c.delete_cluster(self.cluster_id)) is not None:
            self.notify("Cluster deleted")
            self.app.pop_screen()
            self.gh.action_refresh()

    def action_export_stats(self) -> None:
        self.run_worker(self._export_stats(), group="act")

    async def _export_stats(self) -> None:
        csv = await self.gh.api(lambda c: c.stats_export(self.cluster_id, days=30))
        if csv is None:
            return
        slug = re.sub(r"[^a-z0-9]+", "-", (self.summary.name if self.summary else str(self.cluster_id)).lower()).strip(
            "-"
        )
        path = Path.cwd() / f"greenhouse-{slug}-stats-30d.csv"
        await asyncio.to_thread(path.write_text, csv, encoding="utf-8")
        self.notify(f"Exported {path}")
