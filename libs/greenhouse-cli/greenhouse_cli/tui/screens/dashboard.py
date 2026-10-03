"""Dashboard: system pulse + one animated card per cluster."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, VerticalScroll
from textual.widgets import Footer, Header, Static

from greenhouse_cli.tui import resources
from greenhouse_cli.tui.model import ClusterSummary, summarize
from greenhouse_cli.tui.screens.base import DataScreen
from greenhouse_cli.tui.widgets import Banner, ClusterCard


class DashboardScreen(DataScreen):
    """Landing screen — every cluster at a glance."""

    AUTO_REFRESH = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("n", "new_cluster", "New cluster"),
        Binding("S", "sync", "Sync sensors"),
        Binding("c", "check_all", "Check all"),
        Binding("X", "stop_all", "STOP ALL"),
        Binding("right,down,j", "app.focus_next", "Next", show=False),
        Binding("left,up,k", "app.focus_previous", "Previous", show=False),
    ]

    def compose(self) -> ComposeResult:
        """Lay out the health banner above the scrolling grid of cluster cards."""
        yield Header(show_clock=True)
        yield Banner(id="banner")
        with VerticalScroll(id="dashboard-scroll", can_focus=False):
            yield Static("Loading clusters…", id="dashboard-empty")
            yield Grid(id="cluster-grid")
        yield Footer()

    async def load(self) -> None:
        """Fetch system health and every cluster's status + 24h chart concurrently, then rebuild the cards."""
        banner = self.query_one(Banner)
        health, clusters = await asyncio.gather(
            self.gh.api(lambda c: c.system_health(), quiet=True),
            self.gh.api(lambda c: c.list_clusters()),
        )
        banner.update_health(health, self.gh.server_url)
        if clusters is None:
            self.query_one("#dashboard-empty", Static).update("Cannot reach the server — press r to retry.")
            return
        summaries = await asyncio.gather(*(self._summary(cluster["id"]) for cluster in clusters))
        await self._render_cards([s for s in summaries if s is not None])

    async def _summary(self, cluster_id: int) -> ClusterSummary | None:
        status, chart = await asyncio.gather(
            self.gh.api(lambda c: c.status(cluster_id)),
            self.gh.api(lambda c: c.cluster_chart_data(cluster_id, hours=24), quiet=True),
        )
        return summarize(status, chart) if status else None

    async def _render_cards(self, summaries: list[ClusterSummary]) -> None:
        grid = self.query_one("#cluster-grid", Grid)
        focused = self.focused.id if isinstance(self.focused, ClusterCard) else None
        await grid.remove_children()
        self.query_one("#dashboard-empty", Static).display = not summaries
        if not summaries:
            self.query_one("#dashboard-empty", Static).update("No clusters yet — press n to create one.")
            return
        await grid.mount_all(ClusterCard(s) for s in summaries)
        target = self.query(f"#{focused}").first() if focused else None
        (target or grid.children[0]).focus()

    def on_cluster_card_selected(self, message: ClusterCard.Selected) -> None:
        """Open the selected cluster's detail screen."""
        from greenhouse_cli.tui.screens.cluster import ClusterScreen

        self.app.push_screen(ClusterScreen(message.cluster_id))

    def action_new_cluster(self) -> None:
        """Create a cluster from a form; plants are added from its screen."""
        self.form_then(
            "New cluster",
            resources.cluster_fields(),
            lambda v: lambda c: c.create_cluster(v["name"], v["location"], v["environment"]),
            lambda r: f"Created {r.get('name')} — open it and add plants with n",
            "Create",
        )

    def action_sync(self) -> None:
        """Pull fresh sensor readings from the cloud for every cluster."""
        self.notify("Syncing sensors from the cloud…")
        self.run_worker(
            self.act(
                lambda c: c.sync(),
                lambda r: f"Sync done: {r.get('total_new', 0)} new readings, {len(r.get('errors', []))} errors",
            ),
            group="act",
        )

    def action_check_all(self) -> None:
        """Run the scheduled check on every cluster after confirmation (auto-run clusters may water)."""
        self.confirm_then(
            "Run the check on [b]every[/b] cluster?\nClusters with auto-run enabled may irrigate.",
            lambda c: c.check(),
            lambda r: "Check complete" + (" — alerts raised" if r.get("has_alerts") else ""),
            "Check all",
        )

    def action_stop_all(self) -> None:
        """Emergency stop: switch every irrigator off after confirmation."""
        self.confirm_then(
            "[b red]Emergency stop[/b red] — stop every irrigator now?",
            lambda c: c.bulk_stop_all(),
            "Stop-all sent",
            "STOP ALL",
        )
