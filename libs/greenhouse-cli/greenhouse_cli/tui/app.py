"""The Textual application: screens, global key bindings and the API bridge.

Like the rest of the CLI this is a pure HTTP client of ``/api/v1`` — it never
imports ``greenhouse_core`` — so it works against a server anywhere on the
network. Blocking ``IrrigationClient`` calls run in a worker thread via
:meth:`GreenhouseApp.api` so the UI never freezes on a slow link.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, ClassVar

from textual.app import App
from textual.binding import Binding, BindingType

from greenhouse_cli.client import IrrigationClient, ServerError, store_token
from greenhouse_cli.tui.screens.activity import ActivityScreen
from greenhouse_cli.tui.screens.alerts import AlertsScreen
from greenhouse_cli.tui.screens.dashboard import DashboardScreen
from greenhouse_cli.tui.screens.modals import LoginScreen
from greenhouse_cli.tui.screens.search import SearchScreen
from greenhouse_cli.tui.screens.settings import SettingsScreen
from greenhouse_cli.tui.screens.system import SystemScreen

if TYPE_CHECKING:
    from textual.screen import Screen

ClientFactory = Callable[[str | None], IrrigationClient]


class GreenhouseApp(App[None]):
    """Full-screen terminal UI for a greenhouse server."""

    TITLE = "greenhouse"
    CSS_PATH = "app.tcss"
    MODES: ClassVar[dict[str, str | Callable[[], Screen[Any]]]] = {
        "dashboard": DashboardScreen,
        "alerts": AlertsScreen,
        "activity": ActivityScreen,
        "system": SystemScreen,
        "settings": SettingsScreen,
    }
    DEFAULT_MODE = "dashboard"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("d", "switch_mode('dashboard')", "Dashboard"),
        Binding("a", "switch_mode('alerts')", "Alerts"),
        Binding("l", "switch_mode('activity')", "Activity"),
        Binding("s", "switch_mode('system')", "System"),
        Binding("o", "switch_mode('settings')", "Settings"),
        Binding("slash", "search", "Search"),
        Binding("r", "refresh", "Refresh"),
        Binding("question_mark", "show_help_panel", "Keys", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        server_url: str,
        client_factory: ClientFactory | None = None,
        refresh_seconds: float = 30.0,
        animations: bool = True,
    ) -> None:
        """Point the app at ``server_url``; tests inject ``client_factory`` to avoid the network."""
        super().__init__()
        self.server_url = server_url
        self.client_factory: ClientFactory = client_factory or (
            lambda token: IrrigationClient(base_url=server_url, token=token)
        )
        self.client = self.client_factory(None)
        self._retired_clients: list[IrrigationClient] = []
        self.refresh_seconds = refresh_seconds
        self.animations = animations
        self._login_open = False
        # True from a 401 until a sign-in succeeds: screens can then say "sign in"
        # instead of "cannot reach the server" when a call returned nothing.
        self.needs_sign_in = False
        self.sub_title = server_url

    async def api(self, fn: Callable[[IrrigationClient], Any], *, quiet: bool = False) -> Any:
        """Run ``fn(client)`` in a thread; surface errors as notifications.

        Args:
            fn: Callable receiving the live :class:`IrrigationClient`.
            quiet: Suppress the error toast (the caller renders a fallback).

        Returns:
            The decoded response, or ``None`` when the call failed.
        """
        try:
            return await asyncio.to_thread(fn, self.client)
        except ServerError as e:
            if e.status_code == HTTPStatus.UNAUTHORIZED:
                self.needs_sign_in = True
                self.prompt_login()
            elif not quiet:
                self.notify(str(e.detail), title=f"Server error {e.status_code or ''}".strip(), severity="error")
            return None

    def prompt_login(self, error: str | None = None) -> None:
        """Show the sign-in dialog (once) after the server answered 401."""
        if self._login_open:
            return
        self._login_open = True
        self.push_screen(LoginScreen(self.server_url, error), self._on_login)

    def _on_login(self, credentials: tuple[str, str] | None) -> None:
        """Login-dialog callback: re-arm the 401 prompt and sign in unless it was cancelled."""
        self._login_open = False
        if credentials:
            self.run_worker(self._login(*credentials), exclusive=True, group="login")

    async def _login(self, username: str, password: str) -> None:
        """Sign in with a token-less client, store the token and swap in an authenticated client.

        A failed login reopens the dialog with the server's message; on success the active screen reloads.
        """
        with self.client_factory("") as anonymous:
            try:
                data = await asyncio.to_thread(anonymous.login, username, password)
            except ServerError as e:
                self.prompt_login(f"Login failed: {e.detail}")
                return
        token = data.get("access_token", "")
        if token:
            store_token(token)
        # The signed-out client may still serve an in-flight call on a worker thread,
        # so it is retired here and closed with the others when the app exits.
        self._retired_clients.append(self.client)
        self.client = self.client_factory(token or None)
        self.needs_sign_in = False
        self.notify(f"Signed in as {data.get('username', username)}")
        self.action_refresh()

    def on_unmount(self) -> None:
        """Close every HTTP client the session opened (their connection pools) when the app exits."""
        for client in (*self._retired_clients, self.client):
            client.http.close()

    def action_search(self) -> None:
        """Open global search; picking a hit opens its cluster."""
        self.push_screen(SearchScreen(), self._open_cluster)

    def _open_cluster(self, cluster_id: int | None) -> None:
        """Search callback: open the picked cluster on top of the dashboard (cancel = no-op)."""
        if cluster_id is None:
            return
        from greenhouse_cli.tui.screens.cluster import ClusterScreen

        self.switch_mode("dashboard")
        self.push_screen(ClusterScreen(cluster_id))

    def action_refresh(self) -> None:
        """Ask the active screen to reload its data."""
        reload = getattr(self.screen, "reload", None)
        if callable(reload):
            reload()
