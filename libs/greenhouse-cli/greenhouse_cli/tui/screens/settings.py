"""Settings: account, preferences, global irrigation defaults, vacation windows."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from greenhouse_cli.client import clear_stored_token
from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui import resources
from greenhouse_cli.tui.screens.base import DataScreen
from greenhouse_cli.tui.widgets import KeyValue, refill, selected_key


class SettingsScreen(DataScreen):
    """Edit preferences (``p``), global defaults (``g``) and vacation windows (``n``/``u``/``del``)."""

    BINDINGS = [
        Binding("p", "edit_preferences", "Preferences"),
        Binding("g", "edit_global", "Global config"),
        Binding("n", "new_vacation", "New vacation"),
        Binding("u", "edit_vacation", "Edit vacation"),
        Binding("delete", "delete_vacation", "Delete vacation"),
        Binding("O", "logout", "Log out"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.prefs: dict = {}
        self.global_config: dict = {}
        self.vacations: list[dict] = []

    def compose(self) -> ComposeResult:
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
        self.query_one("#vacation-table", DataTable).add_columns("ID", "Starts", "Ends", "Contact", "Notes", "State")

    async def load(self) -> None:
        api = self.gh.api
        me, prefs, global_config, vacation = await asyncio.gather(
            api(lambda c: c.whoami(), quiet=True),
            api(lambda c: c.get_preferences()),
            api(lambda c: c.get_global_config()),
            api(lambda c: c.list_vacation()),
        )
        who = (me or {}).get("username")
        self.query_one("#account", Static).update(
            f"Signed in as [b]{who}[/b] on {self.gh.server_url}   [dim]O: log out[/dim]"
            if who
            else f"Server {self.gh.server_url}  [dim](auth disabled or not signed in)[/dim]"
        )
        self.prefs = prefs or {}
        self.query_one("#prefs-panel", KeyValue).show(
            [(k, str(v)) for k, v in sorted(self.prefs.items())] or [("preferences", "unavailable")],
            title="Preferences  (p to edit)",
        )
        self.global_config = global_config or {}
        self.query_one("#global-panel", KeyValue).show(
            [
                (k, Text("built-in default", style="dim") if v is None else str(v))
                for k, v in sorted(self.global_config.items())
                if k not in {"id", "last_updated"}
            ]
            or [("config", "unavailable")],
            title="Global irrigation defaults  (g to edit)",
        )
        self.vacations = (vacation or {}).get("items", [])
        active_id = ((vacation or {}).get("active") or {}).get("id")
        table = self.query_one("#vacation-table", DataTable)
        rows: list = []
        now = fmt.now()
        for v in self.vacations:
            if v["id"] == active_id:
                state = Text("active", style="bold #7ed957")
            elif v["ends_at"] < now:
                state = Text("past", style="dim")
            else:
                state = Text(f"starts {fmt.ago(v['starts_at'])}", style="#6fb7ff")
            rows.append(
                (
                    str(v["id"]),
                    [
                        str(v["id"]),
                        fmt.clock(v["starts_at"], True),
                        fmt.clock(v["ends_at"], True),
                        v.get("contact_email") or "—",
                        v.get("notes") or "",
                        state,
                    ],
                )
            )
        refill(table, rows)

    def action_edit_preferences(self) -> None:
        self.form_then(
            "Preferences",
            resources.preference_fields(self.prefs),
            lambda v: lambda c: c.update_preferences(**v),
            "Preferences saved",
        )

    def action_edit_global(self) -> None:
        self.form_then(
            "Global irrigation defaults",
            resources.config_fields(self.global_config),
            lambda v: lambda c: c.update_global_config(**v),
            "Global config saved",
            note="Clusters inherit these unless they override a field. Blank = keep current.",
        )

    def _selected_vacation(self) -> dict | None:
        key = selected_key(self.query_one("#vacation-table", DataTable))
        found = next((v for v in self.vacations if str(v["id"]) == key), None)
        if found:
            return found
        self.notify("Select a vacation window first.", severity="warning")
        return None

    def action_new_vacation(self) -> None:
        self.form_then(
            "New vacation window",
            resources.vacation_fields(),
            lambda v: lambda c: c.add_vacation(**v),
            "Vacation window added",
            "Add",
        )

    def action_edit_vacation(self) -> None:
        window = self._selected_vacation()
        if window:
            self.form_then(
                f"Edit vacation #{window['id']}",
                resources.vacation_fields(window),
                lambda v: lambda c: c.update_vacation(window["id"], **v),
                "Vacation window updated",
            )

    def action_delete_vacation(self) -> None:
        window = self._selected_vacation()
        if window:
            self.confirm_then(
                f"Delete vacation window #{window['id']}?",
                lambda c: c.delete_vacation(window["id"]),
                "Vacation window deleted",
                "Delete",
            )

    def action_logout(self) -> None:
        self.run_worker(self._logout(), group="act")

    async def _logout(self) -> None:
        await self.gh.api(lambda c: c.logout(), quiet=True)
        removed = await asyncio.to_thread(clear_stored_token)
        self.gh.client = self.gh.client_factory("")
        self.notify("Logged out" + (" — token removed" if removed else ""))
        self.reload()
