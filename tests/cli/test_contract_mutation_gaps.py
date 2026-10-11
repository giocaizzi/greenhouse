"""Mutation-campaign gap tests (CLI / TUI) — characterization, current behavior.

Each test kills one mutant that survived the original characterization suite (the
mutant id opens each docstring). Pure functions use
explicit reference timestamps; the one Textual test hosts a probe ``DataScreen``
in a minimal app (no server, no client, no network).
"""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App
from textual.widgets import Input, Static

from greenhouse_cli.client import ServerError
from greenhouse_cli.tui.app import GreenhouseApp
from greenhouse_cli.tui.formatting import num
from greenhouse_cli.tui.model import is_watering
from greenhouse_cli.tui.screens.base import DataScreen
from greenhouse_cli.tui.screens.dashboard import DashboardScreen
from greenhouse_cli.tui.screens.modals import LoginScreen
from greenhouse_cli.tui.screens.settings import SettingsScreen

MIN_TIMER_RELOADS = 2  # the mount load plus at least one timer reload in half a second

T0 = 1_776_247_200  # 2026-04-15T10:00:00Z (golden.FROZEN_INSTANT)


@pytest.mark.parametrize(("elapsed", "watering"), [(0, True), (299, True), (300, False), (301, False)])
def test_is_watering_window_is_end_exclusive(elapsed, watering):
    """tui-02: a 5-minute ``start`` counts as watering for ``[ts, ts + 300)``."""
    event = {"action": "start", "duration_minutes": 5, "timestamp": T0}
    assert is_watering(event, reference=T0 + elapsed) is watering


def test_num_formats_ints_without_decimals():
    """tui-14: ints render as integers regardless of ``digits``; floats use ``digits``."""
    assert [num(5, "%"), num(5.26, "%"), num(5.26, digits=0), num(None)] == ["5%", "5.3%", "5", "—"]


class _HostApp(App):
    """Minimal host exposing the one attribute ``DataScreen.on_mount`` reads (``refresh_seconds``)."""

    refresh_seconds = 0.1

    def __init__(self, screen_cls) -> None:
        super().__init__()
        self._screen_cls = screen_cls

    def on_mount(self) -> None:
        self.push_screen(self._screen_cls())


def _reloads_in_half_a_second(*, auto_refresh: bool) -> int:
    reloads: list[int] = []

    class Probe(DataScreen):
        AUTO_REFRESH = auto_refresh

        def compose(self):
            yield Static("probe")

        def reload(self) -> None:  # the timer target; counted instead of hitting the API
            reloads.append(1)

    async def scenario():
        app = _HostApp(Probe)
        async with app.run_test() as pilot:
            await pilot.pause(0.6)

    asyncio.run(scenario())
    return len(reloads)


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(
    ("username", "password", "dismissed"),
    [("admin", "", []), ("", "secret", []), ("  admin ", "s3cret ", [("admin", "s3cret ")])],
)
def test_login_dialog_needs_both_fields(username, password, dismissed):
    """tui-23: the sign-in dialog dismisses only with a non-blank username AND a password.

    The username is stripped, the password is passed verbatim.
    """
    results: list = []

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(LoginScreen("http://test.invalid"), results.append)

    async def scenario():
        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#username", Input).value = username
            screen.query_one("#password", Input).value = password
            await pilot.click("#login")
            await pilot.pause()

    asyncio.run(scenario())
    assert results == dismissed


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(("quiet", "toasts"), [(False, [("boom", "Server error 500", "error")]), (True, [])])
def test_api_error_toast_respects_quiet(quiet, toasts):
    """tui-32: a non-401 ``ServerError`` becomes an error toast unless the caller passed ``quiet=True``.

    Either way ``api`` returns ``None``.
    """
    app = GreenhouseApp("http://test.invalid", client_factory=lambda _token: object(), animations=False)
    seen: list[tuple] = []
    app.notify = lambda message, *, title="", severity="information", **_kw: seen.append((message, title, severity))

    def failing(_client):
        raise ServerError(500, "boom")

    assert asyncio.run(app.api(failing, quiet=quiet)) is None
    assert seen == toasts


@pytest.mark.usefixtures("clean_env")
def test_only_auto_refresh_screens_reload_on_a_timer():
    """tui-17: ``DataScreen.on_mount`` loads once, and installs the periodic reload only when ``AUTO_REFRESH``.

    Settings and the other non-auto screens never poll.
    """
    assert SettingsScreen.AUTO_REFRESH is False
    assert DashboardScreen.AUTO_REFRESH is True
    assert _reloads_in_half_a_second(auto_refresh=False) == 1
    assert _reloads_in_half_a_second(auto_refresh=True) >= MIN_TIMER_RELOADS
