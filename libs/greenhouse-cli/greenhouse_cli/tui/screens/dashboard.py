"""Dashboard: system pulse + one animated card per cluster."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding, BindingType
from textual.containers import Grid, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, Static

from greenhouse_cli.tui import resources
from greenhouse_cli.tui.model import ClusterSummary, summarize
from greenhouse_cli.tui.screens.base import DataScreen
from greenhouse_cli.tui.screens.cluster import ClusterScreen
from greenhouse_cli.tui.widgets import Banner, ClusterCard

if TYPE_CHECKING:
    from textual.app import ComposeResult

logger = logging.getLogger(__name__)

# The server answered 401: it was reached, it wants a login (the sign-in dialog is on top).
SIGN_IN_NEEDED = "Sign in to see your clusters — the server needs a login."


def _log_rebuild_failure(task: asyncio.Future[None]) -> None:
    """Log a card rebuild that failed, with its traceback (also when its worker was cancelled)."""
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.error("dashboard card rebuild failed", exc_info=error)


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

    def __init__(self) -> None:
        """Create the lock that serialises card-grid rebuilds."""
        super().__init__()
        # One card-grid rebuild at a time (S4): an auto-refresh that overtakes a slow
        # load must not interleave remove/mount with it (DuplicateIds / NoMatches).
        self._render_lock = asyncio.Lock()

    async def load(self) -> None:
        """Fetch system health and every cluster's status + 24h chart concurrently, then rebuild the cards."""
        health, clusters = await asyncio.gather(
            self.gh.api(lambda c: c.system_health(), quiet=True),
            self.gh.api(lambda c: c.list_clusters()),
        )
        if not self.showing:
            return
        self.query_one(Banner).update_health(health, self.gh.server_url)
        if clusters is None:
            self.query_one("#dashboard-empty", Static).update(
                SIGN_IN_NEEDED if self.gh.needs_sign_in else "Cannot reach the server — press r to retry."
            )
            return
        summaries = await asyncio.gather(*(self._summary(cluster["id"]) for cluster in clusters))
        # Shielded: when an exclusive reload cancels this worker mid-rebuild, the rebuild still
        # finishes (holding the lock) instead of leaving half-mounted cards for the next one.
        # Nobody awaits it after such a cancel, so its failure is logged by the callback.
        rebuild = asyncio.ensure_future(self._render_cards([s for s in summaries if s is not None]))
        rebuild.add_done_callback(_log_rebuild_failure)
        await asyncio.shield(rebuild)

    async def _summary(self, cluster_id: int) -> ClusterSummary | None:
        """One cluster's card model from its status and 24h soil chart; ``None`` when the status call failed."""
        status, chart = await asyncio.gather(
            self.gh.api(lambda c: c.status(cluster_id)),
            self.gh.api(lambda c: c.cluster_chart_data(cluster_id, hours=24), quiet=True),
        )
        return summarize(status, chart) if status else None

    async def _render_cards(self, summaries: list[ClusterSummary]) -> None:
        """Rebuild the card grid under the render lock; a no-op once the screen is closing."""
        async with self._render_lock:
            try:
                await self._rebuild_cards(summaries)
            except NoMatches:
                if self.showing:
                    raise
                logger.debug("dashboard rebuild dropped: the screen is closing", exc_info=True)

    async def _rebuild_cards(self, summaries: list[ClusterSummary]) -> None:
        """Replace the cards; focus stays on the same cluster when its card is still there, else the first card."""
        if not self.showing:
            return
        grid = self.query_one("#cluster-grid", Grid)
        focused = self.focused.id if isinstance(self.focused, ClusterCard) else None
        await grid.remove_children()
        if not self.showing:
            return
        self.query_one("#dashboard-empty", Static).display = not summaries
        if not summaries:
            self.query_one("#dashboard-empty", Static).update("No clusters yet — press n to create one.")
            return
        await grid.mount_all(ClusterCard(s) for s in summaries)
        matches = self.query(f"#{focused}") if focused else None
        target = matches.first() if matches else None
        if self.showing and grid.children:
            (target or grid.children[0]).focus()

    def on_cluster_card_selected(self, message: ClusterCard.Selected) -> None:
        """Open the selected cluster's detail screen."""
        self.app.push_screen(ClusterScreen(message.cluster_id))

    def action_new_cluster(self) -> None:
        """Create a cluster from a form; plants are added from its screen."""
        self.form_then(
            "New cluster",
            resources.cluster_fields(),
            lambda v: lambda c: c.create_cluster(v["name"], v["location"], v["environment"]),
            lambda r: f"Created {r.get('name')} — open it and add plants with n",
            submit_label="Create",
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
