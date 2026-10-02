"""Characterization goldens for the Textual TUI — screen renders, widget ids, tcss selectors (gap G12).

One headless tour of the real app (seeded in-memory server, fake devices, offline
weather, clock frozen at ``FROZEN_INSTANT`` *before* seeding, ``TZ=UTC``, sprite
animation off, fixed 120×40 terminal) captures:

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

import pytest
import time_machine

from cli.test_contract_tui import make_seeded_app, make_tui, screen_text, settle, wait_until
from cli.tui_fixtures import tui_client_factory
from golden import ENV_PREFIXES, FROZEN_INSTANT, assert_golden, assert_golden_json
from greenhouse_cli import tui as tui_pkg
from greenhouse_cli.tui.screens.cluster import ClusterScreen
from greenhouse_cli.tui.screens.modals import LoginScreen

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


def _dom_ids(screen) -> list[str]:
    """``Type#id.class`` for every node on ``screen`` that carries an id or a non-internal class."""
    seen = set()
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

    def __init__(self, http) -> None:
        self.open = threading.Event()
        self.open.set()
        self._factory = tui_client_factory(http)

    def factory(self, token: str | None):
        client = self._factory(token)
        search = client.search

        def gated(*args, **kwargs):
            if not self.open.wait(timeout=60):
                raise AssertionError("search gate never reopened")
            return search(*args, **kwargs)

        client.search = gated
        return client


def _only_live_search(tui, query: str) -> bool:
    """True once the worker for ``query`` exists and every other search worker is cancelled or done."""
    live = [w for w in tui.workers if w.group == "search" and not w.is_cancelled and not w.is_finished]
    return [w.description for w in live] == [f"search({query!r})"]


async def _tour(http, captures: dict[str, tuple[str, list[str]]]) -> None:
    gate = _SearchGate(http)
    tui = make_tui(http, factory=gate.factory)

    def snap(name: str) -> None:
        captures[name] = (screen_text(tui), _dom_ids(tui.screen))

    async with tui.run_test(size=SIZE) as pilot:
        await settle(pilot, tui)
        snap("dashboard")

        await pilot.press("X")
        await settle(pilot, tui)
        snap("confirm_stop_all")
        await pilot.press("n")
        await settle(pilot, tui)

        await pilot.press("n")
        await settle(pilot, tui)
        snap("form_new_cluster")
        await pilot.press("escape")
        await settle(pilot, tui)

        await pilot.press("slash")
        await settle(pilot, tui)
        gate.open.clear()
        await pilot.press(*"citrus")
        await wait_until(
            lambda: _only_live_search(tui, "citrus"), "the search worker for 'citrus' to be the only live one"
        )
        gate.open.set()
        await pilot.pause(0.4)
        await settle(pilot, tui)
        snap("search_citrus")
        await pilot.press("escape")
        await settle(pilot, tui)

        for key, name in (("a", "alerts"), ("l", "activity"), ("s", "system"), ("o", "settings")):
            await pilot.press(key)
            await settle(pilot, tui)
            snap(name)
        await pilot.press("d")
        await settle(pilot, tui)

        await tui.push_screen(ClusterScreen(1))
        await settle(pilot, tui)
        for tab in CLUSTER_TABS:
            tui.screen.query_one("TabbedContent").active = tab
            await settle(pilot, tui)
            snap(f"cluster_1_{tab.removeprefix('tab-')}")
        tui.screen.query_one("TabbedContent").active = "tab-overview"
        await settle(pilot, tui)
        for key, name in (("i", "irrigate"), ("w", "water_now")):
            await pilot.press(key)
            await settle(pilot, tui)
            snap(name)
            await pilot.press("escape")
            await settle(pilot, tui)
        tui.pop_screen()
        await settle(pilot, tui)

        for cluster_id in (2, 3):
            await tui.push_screen(ClusterScreen(cluster_id))
            await settle(pilot, tui)
            snap(f"cluster_{cluster_id}_overview")
            tui.pop_screen()
            await settle(pilot, tui)


async def _login_tour(http, captures) -> None:
    tui = make_tui(http)
    async with tui.run_test(size=SIZE) as pilot:
        await settle(pilot, tui)
        assert isinstance(tui.screen, LoginScreen)
        captures["login"] = (screen_text(tui), _dom_ids(tui.screen))


@pytest.fixture(scope="module")
def captures() -> dict[str, tuple[str, list[str]]]:
    """Run the tours once per module (per xdist worker) under a frozen clock and a hermetic env."""
    out: dict[str, tuple[str, list[str]]] = {}
    with pytest.MonkeyPatch.context() as mp, time_machine.travel(FROZEN_INSTANT, tick=False):
        tmp = Path(tempfile.mkdtemp(prefix="tui-contract-"))
        _hermetic_env(mp, tmp)
        try:
            http, _, engine = make_seeded_app()
            try:
                asyncio.run(_tour(http, out))
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
def test_screen_render_golden(captures, name):
    """Plain-text render of each screen / tab / dialog == golden."""
    assert name in captures, f"tour did not reach {name}"
    assert_golden(f"tui/screens/{name}.txt", captures[name][0])


def test_tour_covers_exactly_the_named_screens(captures):
    assert sorted(captures) == sorted(SCREEN_NAMES)


_TCSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _tcss_selectors() -> list[str]:
    css = _TCSS_COMMENT.sub("", (Path(tui_pkg.__file__).parent / "app.tcss").read_text(encoding="utf-8"))
    return [" ".join(m.group(1).split()) for m in re.finditer(r"([^{}]+)\{", css)]


def test_ids_and_selectors_golden(captures):
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
