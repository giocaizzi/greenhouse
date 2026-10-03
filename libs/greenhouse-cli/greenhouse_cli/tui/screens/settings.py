"""Settings: account, preferences, global irrigation defaults, vacation windows."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from greenhouse_cli.client import clear_stored_token
from greenhouse_cli.tui import render, resources
from greenhouse_cli.tui.screens.base import DataScreen
from greenhouse_cli.tui.widgets import KeyValue, refill, selected_key


class SettingsScreen(DataScreen):
    """Edit preferences (``p``), global defaults (``g``) and vacation windows (``n``/``u``/``del``)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("p", "edit_preferences", "Preferences"),
        Binding("g", "edit_global", "Global config"),
        Binding("n", "new_vacation", "New vacation"),
        Binding("u", "edit_vacation", "Edit vacation"),
        Binding("delete", "delete_vacation", "Delete vacation"),
        Binding("O", "logout", "Log out"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.prefs: dict[str, Any] = {}
        self.global_config: dict[str, Any] = {}
        self.vacations: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        """Lay out the account line, the preference / global panels and the vacation table."""
        yield Header(show_clock=True)
        with VerticalScroll():
            yield Static(id="account", classes="hint")
            with Horizontal(id="settings-panels"):
                yield KeyValue(id="prefs-panel", classes="panel")
                yield KeyValue(id="global-panel", classes="panel")
            yield Static(
                "[b]Vacation windows[/b]  [dim]rationed watering while you're away · n add · u edit · del delete[/dim]",
                classes="hint",
            )
            yield DataTable(id="vacation-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        """Declare the vacation table columns."""
        self.query_one("#vacation-table", DataTable).add_columns("ID", "Starts", "Ends", "Contact", "Notes", "State")

    async def load(self) -> None:
        """Fetch account, preferences, global config and vacation windows concurrently and render them."""
        api = self.gh.api
        me, prefs, global_config, vacation = await asyncio.gather(
            api(lambda c: c.whoami(), quiet=True),
            api(lambda c: c.get_preferences()),
            api(lambda c: c.get_global_config()),
            api(lambda c: c.list_vacation()),
        )
        who = (me or {}).get("username")
        self.query_one("#account", Static).update(render.account_line(who, self.gh.server_url))
        self.prefs = prefs or {}
        self.query_one("#prefs-panel", KeyValue).show(
            render.preference_rows(self.prefs), title="Preferences  (p to edit)"
        )
        self.global_config = global_config or {}
        self.query_one("#global-panel", KeyValue).show(
            render.global_config_rows(self.global_config), title="Global irrigation defaults  (g to edit)"
        )
        self.vacations = (vacation or {}).get("items", [])
        active_id = ((vacation or {}).get("active") or {}).get("id")
        table = self.query_one("#vacation-table", DataTable)
        refill(table, render.vacation_rows(self.vacations, active_id, self.prefs.get("timezone")))

    def action_edit_preferences(self) -> None:
        """Edit the server-wide preferences."""
        self.form_then(
            "Preferences",
            resources.preference_fields(self.prefs),
            lambda v: lambda c: c.update_preferences(**v),
            "Preferences saved",
        )

    def action_edit_global(self) -> None:
        """Edit the global irrigation defaults that clusters inherit."""
        self.form_then(
            "Global irrigation defaults",
            resources.config_fields(self.global_config),
            lambda v: lambda c: c.update_global_config(**v),
            "Global config saved",
            note="Clusters inherit these unless they override a field. Blank = keep current.",
        )

    def _selected_vacation(self) -> dict[str, Any] | None:
        key = selected_key(self.query_one("#vacation-table", DataTable))
        found = next((v for v in self.vacations if str(v["id"]) == key), None)
        if found:
            return found
        self.notify("Select a vacation window first.", severity="warning")
        return None

    def action_new_vacation(self) -> None:
        """Add a vacation window, entered in the server's timezone preference."""
        self.form_then(
            "New vacation window",
            resources.vacation_fields(tz=self.prefs.get("timezone")),
            lambda v: lambda c: c.add_vacation(**v),
            "Vacation window added",
            "Add",
        )

    def action_edit_vacation(self) -> None:
        """Edit the selected vacation window."""
        window = self._selected_vacation()
        if window:
            self.form_then(
                f"Edit vacation #{window['id']}",
                resources.vacation_fields(window, tz=self.prefs.get("timezone")),
                lambda v: lambda c: c.update_vacation(window["id"], **v),
                "Vacation window updated",
            )

    def action_delete_vacation(self) -> None:
        """Delete the selected vacation window after confirmation."""
        window = self._selected_vacation()
        if window:
            self.confirm_then(
                f"Delete vacation window #{window['id']}?",
                lambda c: c.delete_vacation(window["id"]),
                "Vacation window deleted",
                "Delete",
            )

    def action_logout(self) -> None:
        """Log out on the server, forget the stored token and reload as anonymous."""
        self.run_worker(self._logout(), group="act")

    async def _logout(self) -> None:
        await self.gh.api(lambda c: c.logout(), quiet=True)
        removed = await asyncio.to_thread(clear_stored_token)
        self.gh.client = self.gh.client_factory("")
        self.notify("Logged out" + (" — token removed" if removed else ""))
        self.reload()
