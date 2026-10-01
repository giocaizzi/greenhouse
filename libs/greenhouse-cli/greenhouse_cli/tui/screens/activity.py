"""Activity timeline — the cross-cutting event stream, newest first."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static

from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui.screens.base import DataScreen

SEVERITY_FILTERS: list[str | None] = [None, "warning", "error", "info"]
PAGE = 100


class ActivityScreen(DataScreen):
    """Scrollable activity feed — ``n`` loads older events, ``f`` filters severity."""

    AUTO_REFRESH = True
    BINDINGS = [
        Binding("f", "cycle_filter", "Severity"),
        Binding("n", "more", "Older"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.severity: str | None = None
        self.cursor: int | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="activity-hint", classes="hint")
        yield DataTable(id="activity-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("When", "Severity", "Source", "Entity", "Code", "Message")

    async def load(self) -> None:
        self.cursor = None
        self.query_one(DataTable).clear()
        await self._fetch()

    async def _fetch(self) -> None:
        severity, before = self.severity, self.cursor
        data = await self.gh.api(lambda c: c.list_activity(limit=PAGE, before=before, severity=severity))
        if data is None:
            return
        table = self.query_one(DataTable)
        for e in data.get("items", []):
            entity = e["entity_type"] + (f" #{e['entity_id']}" if e.get("entity_id") is not None else "")
            table.add_row(
                fmt.clock(e["timestamp"], with_date=True),
                fmt.styled(e["severity"], fmt.SEVERITY_STYLES),
                e["source"],
                entity,
                e["code"],
                e["message"],
            )
        self.cursor = data.get("next_cursor")
        more = "n: load older" if self.cursor else "end of feed"
        self.query_one("#activity-hint", Static).update(
            f"[b]{table.row_count} events[/b] · severity [b]{self.severity or 'all'}[/b]   [dim]f: filter  {more}[/dim]"
        )

    def action_cycle_filter(self) -> None:
        self.severity = SEVERITY_FILTERS[(SEVERITY_FILTERS.index(self.severity) + 1) % len(SEVERITY_FILTERS)]
        self.reload()

    def action_more(self) -> None:
        if self.cursor:
            self.run_worker(self._fetch(), group="load", exclusive=True)
