"""System: health pulse, scheduler, device freshness, data quality, maintenance actions."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui.screens.base import DataScreen
from greenhouse_cli.tui.widgets import Banner, KeyValue, refill, selected_key


class SystemScreen(DataScreen):
    """Server-wide state and maintenance actions."""

    AUTO_REFRESH = True
    BINDINGS = [
        Binding("p", "toggle_scheduler", "Pause/resume"),
        Binding("S", "sync", "Sync sensors"),
        Binding("P", "plant_sync", "Plant-DB sync"),
        Binding("H", "health_snapshot", "Health snapshot"),
        Binding("delete", "delete_job", "Remove job"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.paused: bool | None = None

    def compose(self) -> ComposeResult:
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
        self.query_one("#jobs-table", DataTable).add_columns("Job", "Trigger", "Next run", "State")
        self.query_one("#devices-table", DataTable).add_columns("ID", "Device", "Status", "Age", "Note")
        self.query_one("#quality-table", DataTable).add_columns("Severity", "Code", "Entity", "Label", "Message")

    async def load(self) -> None:
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
            [
                (
                    "automatic runs",
                    Text("paused", style="bold #e0c341") if self.paused else Text("active", style="bold #7ed957"),
                ),
                ("scheduler", "running" if (health or {}).get("scheduler_running") else "stopped"),
                ("last sync", fmt.ago((health or {}).get("last_sync_at"))),
                ("", Text("p pause/resume · S sync · P plant DB · H health snapshot · del remove job", style="dim")),
            ],
            title="Scheduler",
        )

        table = self.query_one("#jobs-table", DataTable)
        # One row per job id: an unstarted APScheduler can report a pending job twice.
        unique_jobs = {job["id"]: job for job in jobs or []}.values()
        job_rows: list = []
        for job in unique_jobs:
            job_rows.append(
                (
                    job["id"],
                    [
                        job["name"],
                        job["trigger"],
                        job.get("next_run_time") or "—",
                        Text("paused", style="#e0c341") if job.get("paused") else Text("active", style="#7ed957"),
                    ],
                )
            )
        refill(table, job_rows)

        devices = self.query_one("#devices-table", DataTable)
        device_rows: list = []
        for d in (health or {}).get("devices", []):
            device_rows.append(
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
        refill(devices, device_rows)

        issues = (quality or {}).get("issues", [])
        counts = ", ".join(f"{v} {k}" for k, v in sorted(((quality or {}).get("counts") or {}).items())) or "none"
        self.query_one("#quality-hint", Static).update(f"[b]Data quality[/b]  [dim]{counts}[/dim]")
        qtable = self.query_one("#quality-table", DataTable)
        issue_rows: list = []
        for issue in issues:
            entity = issue["entity_type"] + (f" #{issue['entity_id']}" if issue.get("entity_id") is not None else "")
            issue_rows.append(
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
        refill(qtable, issue_rows)

    def action_toggle_scheduler(self) -> None:
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
        self.notify("Syncing sensors from the cloud…")
        self.run_worker(self.act(lambda c: c.sync(), lambda r: f"Sync done: {r.get('total_new', 0)} new readings"))

    def action_plant_sync(self) -> None:
        self.run_worker(
            self.act(
                lambda c: c.sync_plants(),
                lambda r: f"Plant DB sync: {r.get('synced', 0)} plants updated, {len(r.get('errors', []))} errors",
            )
        )

    def action_health_snapshot(self) -> None:
        self.run_worker(self.act(lambda c: c.health_snapshot(), "Plant health snapshot recorded"))

    def action_delete_job(self) -> None:
        job_id = selected_key(self.query_one("#jobs-table", DataTable))
        if job_id is None:
            return
        self.confirm_then(
            f"Remove scheduler job [b]{job_id}[/b]? It stops firing until the server restarts.",
            lambda c: c.delete_scheduler_job(job_id),
            f"Job {job_id} removed",
            "Remove",
        )
