"""Alerts inbox: browse, acknowledge, resolve, re-scan."""

from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui.screens.base import DataScreen
from greenhouse_cli.tui.widgets import refill, selected_key

STATUS_FILTERS: list[str | None] = ["open", "acknowledged", "resolved", None]


class AlertsScreen(DataScreen):
    """The alert inbox — ``k`` acknowledge, ``v`` resolve, ``f`` filter."""

    AUTO_REFRESH = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("f", "cycle_filter", "Filter"),
        Binding("k", "acknowledge", "Acknowledge"),
        Binding("v", "resolve", "Resolve"),
        Binding("y", "sync_alerts", "Re-scan"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.status_filter: str | None = "open"
        self._alerts: dict[int, dict[str, Any]] = {}

    def compose(self) -> ComposeResult:
        """Lay out the hint line, the alert table and the detail panel below it."""
        yield Header(show_clock=True)
        with Vertical():
            yield Static(id="alerts-hint", classes="hint")
            yield DataTable(id="alerts-table", cursor_type="row", zebra_stripes=True)
            yield Static(id="alert-detail", classes="panel")
        yield Footer()

    def on_mount(self) -> None:
        """Declare the alert table columns (``×`` is the occurrence count)."""
        self.query_one(DataTable).add_columns("ID", "Severity", "Status", "Title", "Cluster", "Last seen", "×")

    async def load(self) -> None:
        """Fetch alerts for the current status filter and keep them by id for the detail panel."""
        status = self.status_filter
        data = await self.gh.api(lambda c: c.list_alerts(status=status, limit=200))
        if data is None:
            return
        self.query_one("#alerts-hint", Static).update(
            f"[b]{data.get('open_count', 0)} open[/b] · showing [b]{status or 'all'}[/b]"
            "   [dim]f: filter  k: acknowledge  v: resolve  y: re-scan[/dim]"
        )
        table = self.query_one(DataTable)
        self._alerts = {a["id"]: a for a in data.get("items", [])}
        rows: list[Any] = [
            (
                str(a["id"]),
                [
                    str(a["id"]),
                    fmt.styled(a["severity"], fmt.SEVERITY_STYLES),
                    fmt.styled(a["status"], fmt.STATUS_STYLES),
                    a["title"],
                    str(a["cluster_id"]) if a.get("cluster_id") is not None else "—",
                    fmt.ago(a["last_seen_at"]),
                    str(a.get("occurrence_count", 1)),
                ],
            )
            for a in data.get("items", [])
        ]
        refill(table, rows)
        if not self._alerts:
            self.query_one("#alert-detail", Static).update(Text("Nothing here — all quiet in the greenhouse.", "dim"))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Show the highlighted alert's full message, origin and first/last-seen times."""
        alert = self._alerts.get(int(event.row_key.value)) if event.row_key.value else None
        if not alert:
            return
        text = Text()
        text.append(f"{alert['title']}\n", style="bold")
        text.append(f"{alert['source']} · {alert['code']} · {alert['entity_type']}", style="dim")
        if alert.get("entity_id") is not None:
            text.append(f" #{alert['entity_id']}", style="dim")
        text.append(f"\nfirst seen {fmt.clock(alert['first_seen_at'], True)} · ")
        text.append(f"last seen {fmt.clock(alert['last_seen_at'], True)}\n\n")
        text.append(alert["message"])
        self.query_one("#alert-detail", Static).update(text)

    def _selected(self) -> int | None:
        """The alert id under the cursor, or ``None`` for an empty table."""
        key = selected_key(self.query_one(DataTable))
        return int(key) if key else None

    def action_cycle_filter(self) -> None:
        """Step through open → acknowledged → resolved → all and reload."""
        self.status_filter = STATUS_FILTERS[(STATUS_FILTERS.index(self.status_filter) + 1) % len(STATUS_FILTERS)]
        self.reload()

    def action_acknowledge(self) -> None:
        """Acknowledge the selected alert; it stays unresolved, so a leak hold stays in force."""
        alert_id = self._selected()
        if alert_id is not None:
            self.run_worker(self.act(lambda c: c.acknowledge_alert(alert_id), f"Alert {alert_id} acknowledged"))

    def action_resolve(self) -> None:
        """Resolve the selected alert; resolving a leak alert is what releases its hold."""
        alert_id = self._selected()
        if alert_id is not None:
            self.run_worker(self.act(lambda c: c.resolve_alert(alert_id), f"Alert {alert_id} resolved"))

    def action_sync_alerts(self) -> None:
        """Ask the server to re-run the alert scan for every cluster now."""
        self.run_worker(self.act(lambda c: c.sync_alerts(), "Alert re-scan complete"))
