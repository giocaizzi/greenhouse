"""Global search across clusters, plants, sensors and irrigators."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, ClassVar, cast

from textual import on, work
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static

if TYPE_CHECKING:
    from greenhouse_cli.tui.app import GreenhouseApp

CLUSTER_HREF = re.compile(r"/clusters/(\d+)")


class SearchScreen(ModalScreen[int | None]):
    """Type to search; ``enter`` on a hit dismisses with the hit's cluster ID."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "dismiss(None)", "Close")]

    def compose(self) -> ComposeResult:
        """Render the search box above the results table."""
        with Vertical(classes="dialog search-dialog"):
            yield Static("[b]Search[/b]  [dim]clusters, plants, sensors, irrigators, device IDs[/dim]")
            yield Input(placeholder="type to search…", id="search-input")
            yield DataTable(id="search-results", cursor_type="row")

    def __init__(self) -> None:
        super().__init__()
        self._targets: dict[str, int] = {}

    def on_mount(self) -> None:
        """Declare the result columns and put the cursor in the search box."""
        self.query_one(DataTable).add_columns("Type", "Name", "Detail")
        self.query_one(Input).focus()

    @on(Input.Changed)
    def _changed(self, event: Input.Changed) -> None:
        """Every keystroke restarts the debounced search worker."""
        self.search(event.value)

    @on(Input.Submitted)
    def _submitted(self) -> None:
        """Enter moves focus to the results, when there are any, so arrows pick a hit."""
        table = self.query_one(DataTable)
        if table.row_count:
            table.focus()

    @work(exclusive=True, group="search")
    async def search(self, query: str) -> None:
        """Debounce keystrokes, then query the server and remember which hits link to a cluster."""
        await asyncio.sleep(0.2)  # debounce keystrokes
        table = self.query_one(DataTable)
        if not query.strip():
            table.clear()
            return
        app = cast("GreenhouseApp", self.app)
        data = await app.api(lambda c: c.search(query, limit=50), quiet=True)
        table.clear()
        self._targets = {}
        for i, hit in enumerate((data or {}).get("hits", [])):
            match = CLUSTER_HREF.search(hit.get("href", ""))
            if match:
                self._targets[str(i)] = int(match.group(1))
            table.add_row(hit["entity_type"], hit["label"], hit.get("sublabel") or "", key=str(i))

    @on(DataTable.RowSelected)
    def _selected(self, event: DataTable.RowSelected) -> None:
        """Dismiss with the hit's cluster id; hits that link to no cluster are ignored."""
        target = self._targets.get(event.row_key.value or "")
        if target is not None:
            self.dismiss(target)
