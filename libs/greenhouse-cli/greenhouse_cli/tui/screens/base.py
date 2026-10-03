"""Shared screen behaviour: typed app access, periodic reload, guarded actions."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from textual.screen import Screen

from greenhouse_cli.tui.screens.forms import Field, FormScreen
from greenhouse_cli.tui.screens.modals import ConfirmScreen

if TYPE_CHECKING:
    from greenhouse_cli.client import IrrigationClient
    from greenhouse_cli.tui.app import GreenhouseApp


class DataScreen(Screen[Any]):
    """A screen that loads its content from the API and can auto-refresh."""

    AUTO_REFRESH = False

    @property
    def gh(self) -> GreenhouseApp:
        """The app with its typed API helpers (``Screen.app`` is typed as a plain ``App``)."""
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        """Load once on mount and, for auto-refresh screens, poll at the app's refresh interval."""
        # Textual dispatches on_mount to every class in the MRO, so subclasses
        # define their own on_mount (table columns etc.) without calling super.
        self.reload()
        if self.AUTO_REFRESH and self.gh.refresh_seconds > 0:
            self.set_interval(self.gh.refresh_seconds, self.reload)

    def reload(self) -> None:
        """Re-run :meth:`load` in an exclusive worker so a slow reload is replaced, never stacked."""
        self.run_worker(self.load(), exclusive=True, group="load")

    async def load(self) -> None:  # pragma: no cover - overridden
        """Fetch and render the screen's data; every concrete screen overrides it."""
        raise NotImplementedError

    def confirm_then(
        self,
        message: str,
        fn: Callable[[IrrigationClient], Any],
        done: str | Callable[[Any], str],
        confirm_label: str = "Confirm",
    ) -> None:
        """Ask for confirmation, then run an actuating call and refresh.

        Args:
            message: Question shown in the dialog.
            fn: API call to make once confirmed.
            done: Toast text (or a callable building it from the response).
            confirm_label: Label for the confirm button.
        """

        def _after(ok: bool | None) -> None:
            if ok:
                self.run_worker(self.act(fn, done), group="act")

        self.app.push_screen(ConfirmScreen(message, confirm_label), _after)

    def form_then(
        self,
        title: str,
        fields: list[Field],
        call: Callable[[dict[str, Any]], Callable[[IrrigationClient], Any]],
        done: str | Callable[[Any], str],
        submit_label: str = "Save",
        note: str | None = None,
    ) -> None:
        """Show a form, then send its values through ``call(values)`` and refresh.

        Args:
            title: Dialog title.
            fields: Inputs to render (pre-filled for edits).
            call: Builds the API call from the parsed form values.
            done: Toast text (or a callable building it from the response).
            submit_label: Label for the submit button.
            note: Optional help line under the title.
        """

        def _after(values: dict[str, Any] | None) -> None:
            if values is not None:
                self.run_worker(self.act(call(values), done), group="act")

        self.app.push_screen(FormScreen(title, fields, submit_label, note), _after)

    async def act(self, fn: Callable[[IrrigationClient], Any], done: str | Callable[[Any], str]) -> Any:
        """Run an action call, toast the outcome and reload."""
        result = await self.gh.api(fn)
        if result is not None:
            self.notify(done(result) if callable(done) else done)
            self.reload()
        return result
