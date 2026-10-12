"""Characterization goldens for the Textual TUI — screen renders, widget ids, tcss selectors.

One headless tour of the real app (seeded in-memory server, fake devices, offline
weather, clock frozen at ``FROZEN_INSTANT`` *before* seeding, ``TZ=UTC``, sprite
animation off, fixed 120x40 terminal) captures:

- ``tests/golden/tui/screens/<name>.txt`` — a plain-text export of every screen,
  every cluster tab and every dialog;
- ``tests/golden/tui/ids_and_selectors.json`` — the widget ids / classes present on
  each mounted screen (what ``app.tcss`` styles), the selectors declared in
  ``app.tcss`` in order, and which tcss ids/classes match no mounted widget.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import time_machine
from textual.widgets import TabbedContent

from cli.test_contract_tui import make_seeded_app, make_tui, screen_text, settle, wait_until
from cli.tui_fixtures import tui_client_factory
from golden import ENV_PREFIXES, FROZEN_INSTANT, assert_golden, assert_golden_json
from greenhouse_cli import tui as tui_pkg
from greenhouse_cli.tui.screens.cluster import ClusterScreen
from greenhouse_cli.tui.screens.modals import LoginScreen

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.testclient import TestClient
    from textual.dom import DOMNode
    from textual.pilot import Pilot

    from greenhouse_cli._client.transport import JSONObject
    from greenhouse_cli.client import IrrigationClient
    from greenhouse_cli.tui.app import GreenhouseApp

Captures = dict[str, tuple[str, list[str]]]  # screen name → (plain-text render, DOM ids)

SIZE = (120, 40)

CLUSTER_TABS = [
    "tab-overview",
    "tab-charts",
    "tab-plants",
    "tab-sensors",
    "tab-insights",
    "tab-decisions",
    "tab-history",
    "tab-windows",
    "tab-config",
]

SCREEN_NAMES = [
    "dashboard",
    "confirm_stop_all",
    "form_new_cluster",
    "search_citrus",
    "alerts",
    "activity",
    "system",
    "settings",
    *[f"cluster_1_{tab.removeprefix('tab-')}" for tab in CLUSTER_TABS],
    "irrigate",
    "water_now",
    "cluster_2_overview",
    "cluster_3_overview",
    "login",
]


def _dom_ids(screen: DOMNode) -> list[str]:
    """``Type#id.class`` for every node on ``screen`` that carries an id or a non-internal class."""
    seen: set[str] = set()
    for node in screen.walk_children(with_self=True):
        node_id = node.id if node.id and not node.id.startswith("-") else ""
        classes = sorted(c for c in node.classes if not c.startswith("-"))
        if not node_id and not classes:
            continue
        seen.add(type(node).__name__ + (f"#{node_id}" if node_id else "") + "".join(f".{c}" for c in classes))
    return sorted(seen)


class _SearchGate:
    """Holds every ``search`` request while the test is typing a query into the search dialog.

    ``DataTable`` column widths are a high-water mark: ``clear()`` keeps the columns and
    ``_update_dimensions`` only ever widens them. The dialog debounces each keystroke by 0.2 s,
    so when a loaded CPU spaces two pilot keystrokes further apart than that, an intermediate
    query (``"c"``, ``"ci"`` …) renders its wider hits first and the final table keeps those widths.
    Holding the responses until the last keystroke's worker is the only live one reproduces the
    unloaded timing (every intermediate worker is cancelled before its rows land) on any machine.
    The requests themselves are unchanged; they are only delayed.
    """

    def __init__(self, http: TestClient, mp: pytest.MonkeyPatch) -> None:
        self.mp = mp
        self.open = threading.Event()
        self.open.set()
        self._factory = tui_client_factory(http)

    def factory(self, token: str | None) -> IrrigationClient:
        client = self._factory(token)
        search: Callable[..., JSONObject] = client.search

        def gated(*args: object, **kwargs: object) -> JSONObject:
            if not self.open.wait(timeout=60):
                msg = "search gate never reopened"
                raise AssertionError(msg)
            return search(*args, **kwargs)

        self.mp.setattr(client, "search", gated)
        return client


def _only_live_search(tui: GreenhouseApp, query: str) -> bool:
    """True once the worker for ``query`` exists and every other search worker is cancelled or done."""
    live = [w for w in tui.workers if w.group == "search" and not w.is_cancelled and not w.is_finished]
    return [w.description for w in live] == [f"search({query!r})"]


class _Tourist:
    """One tour's pilot and app, and the captures it records (plain-text screen + DOM ids per name)."""

    def __init__(self, pilot: Pilot[None], tui: GreenhouseApp, captures: Captures) -> None:
        self.pilot, self.tui, self.captures = pilot, tui, captures

    def snap(self, name: str) -> None:
        self.captures[name] = (screen_text(self.tui), _dom_ids(self.tui.screen))

    async def press(self, *keys: str) -> None:
        await self.pilot.press(*keys)
        await settle(self.pilot, self.tui)


async def _tour(http: TestClient, captures: Captures, mp: pytest.MonkeyPatch) -> None:
    gate = _SearchGate(http, mp)
    tui = make_tui(http, factory=gate.factory)
    async with tui.run_test(size=SIZE) as pilot:
        tourist = _Tourist(pilot, tui, captures)
        await settle(pilot, tui)
        await _tour_dashboard_dialogs(tourist)
        await _tour_search(tourist, gate)
        await _tour_top_level_screens(tourist)
        await _tour_cluster_1(tourist)
        for cluster_id in (2, 3):
            await tui.push_screen(ClusterScreen(cluster_id))
            await settle(pilot, tui)
            tourist.snap(f"cluster_{cluster_id}_overview")
            tui.pop_screen()
            await settle(pilot, tui)


async def _tour_dashboard_dialogs(t: _Tourist) -> None:
    t.snap("dashboard")
    await t.press("X")
    t.snap("confirm_stop_all")
    await t.press("n")
    await t.press("n")
    t.snap("form_new_cluster")
    await t.press("escape")


async def _tour_search(t: _Tourist, gate: _SearchGate) -> None:
    pilot, tui = t.pilot, t.tui
    await t.press("slash")
    gate.open.clear()
    await pilot.press(*"citrus")
    await wait_until(lambda: _only_live_search(tui, "citrus"), "the search worker for 'citrus' to be the only live one")
    gate.open.set()
    await pilot.pause(0.4)
    await settle(pilot, tui)
    t.snap("search_citrus")
    await t.press("escape")


async def _tour_top_level_screens(t: _Tourist) -> None:
    for key, name in (("a", "alerts"), ("l", "activity"), ("s", "system"), ("o", "settings")):
        await t.press(key)
        t.snap(name)
    await t.press("d")


async def _tour_cluster_1(t: _Tourist) -> None:
    pilot, tui = t.pilot, t.tui
    await tui.push_screen(ClusterScreen(1))
    await settle(pilot, tui)
    for tab in CLUSTER_TABS:
        tui.screen.query_one("TabbedContent", TabbedContent).active = tab
        await settle(pilot, tui)
        t.snap(f"cluster_1_{tab.removeprefix('tab-')}")
    tui.screen.query_one("TabbedContent", TabbedContent).active = "tab-overview"
    await settle(pilot, tui)
    for key, name in (("i", "irrigate"), ("w", "water_now")):
        await t.press(key)
        t.snap(name)
        await t.press("escape")
    tui.pop_screen()
    await settle(pilot, tui)


async def _login_tour(http: TestClient, captures: Captures) -> None:
    tui = make_tui(http)
    async with tui.run_test(size=SIZE) as pilot:
        await settle(pilot, tui)
        assert isinstance(tui.screen, LoginScreen)
        captures["login"] = (screen_text(tui), _dom_ids(tui.screen))


@pytest.fixture(scope="module")
def captures() -> Captures:
    """Run the tours once per module (per xdist worker) under a frozen clock and a hermetic env."""
    out: Captures = {}
    with pytest.MonkeyPatch.context() as mp, time_machine.travel(FROZEN_INSTANT, tick=False):
        tmp = Path(tempfile.mkdtemp(prefix="tui-contract-"))
        _hermetic_env(mp, tmp)
        try:
            http, _, engine = make_seeded_app()
            try:
                asyncio.run(_tour(http, out, mp))
            finally:
                engine.dispose()
            http, _, engine = make_seeded_app(bypass_auth=False)
            try:
                asyncio.run(_login_tour(http, out))
            finally:
                engine.dispose()
        finally:
            mp.undo()
            time.tzset()
    return out


def _hermetic_env(mp: pytest.MonkeyPatch, tmp: Path) -> None:
    """Module-scoped twin of the ``clean_env`` fixture (which is function-scoped)."""
    for name in list(os.environ):
        if name.startswith(ENV_PREFIXES):
            mp.delenv(name, raising=False)
    mp.chdir(tmp)
    mp.setenv("HOME", str(tmp))
    mp.setenv("XDG_CONFIG_HOME", str(tmp / "config"))
    mp.setenv("TZ", "UTC")
    time.tzset()


@pytest.mark.parametrize("name", SCREEN_NAMES)
def test_screen_render_golden(captures: Captures, name: str) -> None:
    """Plain-text render of each screen / tab / dialog == golden."""
    assert name in captures, f"tour did not reach {name}"
    assert_golden(f"tui/screens/{name}.txt", captures[name][0])


def test_tour_covers_exactly_the_named_screens(captures: Captures) -> None:
    """The screen tour captures exactly the named screens, no more and no fewer."""
    assert sorted(captures) == sorted(SCREEN_NAMES)


_TCSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _tcss_selectors() -> list[str]:
    css = _TCSS_COMMENT.sub("", (Path(tui_pkg.__file__).parent / "app.tcss").read_text(encoding="utf-8"))
    return [" ".join(m.group(1).split()) for m in re.finditer(r"([^{}]+)\{", css)]


def test_ids_and_selectors_golden(captures: Captures) -> None:
    """Widget ids/classes per mounted screen, the tcss selectors, and tcss ids/classes matching nothing == golden."""
    selectors = _tcss_selectors()
    mounted = {token for _, ids in captures.values() for entry in ids for token in re.findall(r"[#.][\w-]+", entry)}
    referenced = {token for sel in selectors for token in re.findall(r"(?<![\w-])[#.][a-zA-Z][\w-]*", sel)}
    golden = {
        "screens": {name: ids for name, (_, ids) in sorted(captures.items())},
        "tcss_selectors": selectors,
        "tcss_tokens_without_a_mounted_widget": sorted(referenced - mounted),
    }
    assert_golden_json("tui/ids_and_selectors.json", golden)
