"""Modal dialogs: confirmation, irrigation options, manual watering, login."""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Static

from greenhouse_cli.tui.sprites import watering_can_sprite


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no confirmation — every actuating action goes through this."""

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "dismiss(False)", "Cancel"),
        ("y", "dismiss(True)", "Yes"),
        ("n", "dismiss(False)", "No"),
    ]

    def __init__(self, message: str, confirm_label: str = "Confirm", danger: bool = True) -> None:
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label
        self.danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.message, classes="dialog-message")
            with Horizontal(classes="dialog-buttons"):
                yield Button(self.confirm_label, variant="error" if self.danger else "primary", id="confirm")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class IrrigateScreen(ModalScreen[dict[str, Any] | None]):
    """Options for ``POST /clusters/{id}/irrigate`` — dry-run is the safe default."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, cluster_name: str) -> None:
        super().__init__()
        self.cluster_name = cluster_name

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            with Horizontal(classes="dialog-head"):
                yield Static(watering_can_sprite(), classes="dialog-sprite")
                yield Static(
                    f"[b]Smart irrigation — {self.cluster_name}[/b]\n\n"
                    "Runs the full pipeline: sync → decide → actuate.\n"
                    "[dim]Cooldown, caps and leak holds still apply.[/dim]",
                    classes="dialog-message",
                )
            yield Checkbox("Dry run (decide only, no water)", value=True, id="dry-run")
            yield Checkbox("Skip sensor sync", id="no-sync")
            yield Checkbox("Force (bypass quiet hours)", id="force")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Run", variant="primary", id="run")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "run":
            self.dismiss(None)
            return
        self.dismiss(
            {
                "dry_run": self.query_one("#dry-run", Checkbox).value,
                "no_sync": self.query_one("#no-sync", Checkbox).value,
                "force": self.query_one("#force", Checkbox).value,
            }
        )


class WaterNowScreen(ModalScreen[int | None]):
    """Ask how many minutes to run an irrigator manually (blank = device default)."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, irrigator_name: str) -> None:
        super().__init__()
        self.irrigator_name = irrigator_name

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            with Horizontal(classes="dialog-head"):
                yield Static(watering_can_sprite(pouring=True), classes="dialog-sprite")
                yield Static(
                    f"[b]Start {self.irrigator_name} now[/b]\n\n"
                    "Manual start bypasses the decision engine.\n"
                    "[dim]Leave minutes blank for the configured default.[/dim]",
                    classes="dialog-message",
                )
            yield Input(placeholder="minutes", type="integer", id="minutes")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Start", variant="error", id="start")
                yield Button("Cancel", id="cancel")

    def on_input_submitted(self) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self) -> None:
        raw = self.query_one("#minutes", Input).value.strip()
        self.dismiss(int(raw) if raw else 0)


class LoginScreen(ModalScreen[tuple[str, str] | None]):
    """Collect credentials when the server answers 401."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, server: str, error: str | None = None) -> None:
        super().__init__()
        self.server = server
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(f"[b]Sign in[/b]  [dim]{self.server}[/dim]", classes="dialog-message")
            if self.error:
                yield Label(self.error, classes="dialog-error")
            with Grid(classes="login-grid"):
                yield Label("Username")
                yield Input(id="username")
                yield Label("Password")
                yield Input(password=True, id="password")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Sign in", variant="primary", id="login")
                yield Button("Cancel", id="cancel")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "username":
            self.query_one("#password", Input).focus()
        else:
            self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "login":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self) -> None:
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        if username and password:
            self.dismiss((username, password))
