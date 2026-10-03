"""System: health pulse, scheduler, device freshness, data quality, maintenance actions."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from greenhouse_cli.tui import render
from greenhouse_cli.tui.screens.base import DataScreen
from greenhouse_cli.tui.widgets import Banner, KeyValue, refill, selected_key


class SystemScreen(DataScreen):
    """Server-wide state and maintenance actions."""

    AUTO_REFRESH = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("p", "toggle_scheduler", "Pause/resume"),
        Binding("S", "sync", "Sync sensors"),
        Binding("P", "plant_sync", "Plant-DB sync"),
        Binding("H", "health_snapshot", "Health snapshot"),
        Binding("delete", "delete_job", "Remove job"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.paused: bool | None = None
        self.core_jobs: set[str] = set()

    def compose(self) -> ComposeResult:
        """Lay out the health banner, scheduler panel, jobs, devices and data-quality tables."""
        yield Header(show_clock=True)
        yield Banner(id="system-banner")
        with VerticalScroll():
            with Horizontal(id="system-panels"):
                yield KeyValue(id="scheduler-panel", classes="panel")
                yield DataTable(id="jobs-table", cursor_type="row")
            yield Static("[b]Devices[/b]", classes="hint")
            yield DataTable(id="devices-table", cursor_type="row", zebra_stripes=True)
            yield Static(id="quality-hint", classes="hint")
            yield DataTable(id="quality-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        """Declare the jobs, devices and data-quality table columns."""
        self.query_one("#jobs-table", DataTable).add_columns("Job", "Trigger", "Next run", "State")
        self.query_one("#devices-table", DataTable).add_columns("ID", "Device", "Status", "Age", "Note")
        self.query_one("#quality-table", DataTable).add_columns("Severity", "Code", "Entity", "Label", "Message")

    async def load(self) -> None:
        """Fetch health, scheduler jobs, preferences and the data-quality report concurrently and render them."""
        api = self.gh.api
        health, jobs, prefs, quality = await asyncio.gather(
            api(lambda c: c.system_health()),
            api(lambda c: c.scheduler_jobs(), quiet=True),
            api(lambda c: c.get_preferences(), quiet=True),
            api(lambda c: c.quality_report(), quiet=True),
        )
        self.query_one(Banner).update_health(health, self.gh.server_url)

        self.paused = (prefs or {}).get("scheduler_paused")
        self.query_one("#scheduler-panel", KeyValue).show(
            render.scheduler_panel_rows(self.paused, health), title="Scheduler"
        )

        table = self.query_one("#jobs-table", DataTable)
        # One row per job id: an unstarted APScheduler can report a pending job twice.
        unique_jobs = {job["id"]: job for job in jobs or []}.values()
        self.core_jobs = {job["id"] for job in unique_jobs if job.get("core")}
        refill(table, render.job_rows(unique_jobs))

        refill(self.query_one("#devices-table", DataTable), render.device_rows(health))

        issues = (quality or {}).get("issues", [])
        counts = ", ".join(f"{v} {k}" for k, v in sorted(((quality or {}).get("counts") or {}).items())) or "none"
        self.query_one("#quality-hint", Static).update(f"[b]Data quality[/b]  [dim]{counts}[/dim]")
        refill(self.query_one("#quality-table", DataTable), render.quality_rows(issues))

    def action_toggle_scheduler(self) -> None:
        """Resume a paused scheduler at once; pausing asks first because it stops automatic irrigation."""
        if self.paused:
            self.run_worker(self.act(lambda c: c.scheduler_resume(), "Scheduler resumed"))
        else:
            self.confirm_then(
                "Pause automatic irrigation? Sensor sync and monitoring keep running.",
                lambda c: c.scheduler_pause(),
                "Scheduler paused",
                "Pause",
            )

    def action_sync(self) -> None:
        """Pull fresh sensor readings from the cloud."""
        self.notify("Syncing sensors from the cloud…")
        self.run_worker(self.act(lambda c: c.sync(), lambda r: f"Sync done: {r.get('total_new', 0)} new readings"))

    def action_plant_sync(self) -> None:
        """Refresh every plant's care data from the curated plant DB."""
        self.run_worker(
            self.act(
                lambda c: c.sync_plants(),
                lambda r: f"Plant DB sync: {r.get('synced', 0)} plants updated, {len(r.get('errors', []))} errors",
            )
        )

    def action_health_snapshot(self) -> None:
        """Record a plant-health snapshot now instead of waiting for the daily job."""
        self.run_worker(self.act(lambda c: c.health_snapshot(), "Plant health snapshot recorded"))

    def action_delete_job(self) -> None:
        """Remove the selected custom job; built-in jobs are refused with a hint to pause instead."""
        job_id = selected_key(self.query_one("#jobs-table", DataTable))
        if job_id is None:
            return
        if job_id in self.core_jobs:
            self.notify(
                f"{job_id} is a built-in job and can't be removed — press p to pause automatic irrigation instead.",
                severity="warning",
            )
            return
        self.confirm_then(
            f"Remove scheduler job [b]{job_id}[/b]? It won't fire again.",
            lambda c: c.delete_scheduler_job(job_id),
            f"Job {job_id} removed",
            "Remove",
        )
