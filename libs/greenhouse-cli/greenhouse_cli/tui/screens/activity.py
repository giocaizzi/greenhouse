"""Activity timeline — the cross-cutting event stream, newest first."""

from __future__ import annotations

from typing import Any, ClassVar

from rich.console import RenderableType
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import DataTable, Footer, Header, Static

from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui.screens.base import DataScreen
from greenhouse_cli.tui.widgets import refill

SEVERITY_FILTERS: list[str | None] = [None, "warning", "error", "info"]
PAGE = 100


class ActivityScreen(DataScreen):
    """Scrollable activity feed — ``n`` loads older events, ``f`` filters severity."""

    AUTO_REFRESH = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("f", "cycle_filter", "Severity"),
        Binding("n", "more", "Older"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.severity: str | None = None
        self.cursor: int | None = None

    def compose(self) -> ComposeResult:
        """Lay out the hint line above a single scrolling event table."""
        yield Header(show_clock=True)
        yield Static(id="activity-hint", classes="hint")
        yield DataTable(id="activity-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        """Declare the table columns once; rows are appended page by page."""
        self.query_one(DataTable).add_columns("When", "Severity", "Source", "Entity", "Code", "Message")

    async def load(self) -> None:
        """Restart the feed from the newest event (a filter change or refresh drops the page cursor).

        The rows are replaced with :func:`refill`, so an auto-refresh keeps the
        table cursor on the same event instead of snapping back to the top.
        """
        self.cursor = None
        data = await self._page()
        if data is None:
            return
        refill(self.query_one(DataTable), self._rows(data))
        self._after_page(data)

    async def _fetch(self) -> None:
        """Append the page older than the cursor."""
        data = await self._page()
        if data is None:
            return
        table = self.query_one(DataTable)
        for key, cells in self._rows(data):
            table.add_row(*cells, key=key)
        self._after_page(data)

    async def _page(self) -> dict[str, Any] | None:
        """The page of events older than the cursor (``None`` when the call failed)."""
        severity, before = self.severity, self.cursor
        page: dict[str, Any] | None = await self.gh.api(
            lambda c: c.list_activity(limit=PAGE, before=before, severity=severity)
        )
        return page

    @staticmethod
    def _rows(data: dict[str, Any]) -> list[tuple[str | None, list[RenderableType]]]:
        """``(key, cells)`` per event; the event id keys the row so refreshes can find it again."""
        rows: list[tuple[str | None, list[RenderableType]]] = []
        for e in data.get("items", []):
            entity = e["entity_type"] + (f" #{e['entity_id']}" if e.get("entity_id") is not None else "")
            key = str(e["id"]) if e.get("id") is not None else None
            rows.append(
                (
                    key,
                    [
                        fmt.clock(e["timestamp"], with_date=True),
                        fmt.styled(e["severity"], fmt.SEVERITY_STYLES),
                        e["source"],
                        entity,
                        e["code"],
                        e["message"],
                    ],
                )
            )
        return rows

    def _after_page(self, data: dict[str, Any]) -> None:
        """Keep the server's next cursor (``None`` = end of the feed) and refresh the hint line."""
        table = self.query_one(DataTable)
        self.cursor = data.get("next_cursor")
        more = "n: load older" if self.cursor else "end of feed"
        self.query_one("#activity-hint", Static).update(
            f"[b]{table.row_count} events[/b] · severity [b]{self.severity or 'all'}[/b]   [dim]f: filter  {more}[/dim]"
        )

    def action_cycle_filter(self) -> None:
        """Step to the next severity filter and reload from the top."""
        self.severity = SEVERITY_FILTERS[(SEVERITY_FILTERS.index(self.severity) + 1) % len(SEVERITY_FILTERS)]
        self.reload()

    def action_more(self) -> None:
        """Append the next older page, if the server reported a cursor for one."""
        if self.cursor:
            self.run_worker(self._fetch(), group="load", exclusive=True)
