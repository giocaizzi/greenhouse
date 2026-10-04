"""Characterization of TUI runtime behavior: threading (G13), cursor stability (G14), 401 sign-in.

- G13: every blocking ``IrrigationClient`` call runs off the event-loop thread
  (``GreenhouseApp.api`` → ``asyncio.to_thread``).
- G14: a screen refresh keeps the table cursor on the same record (``widgets.refill``),
  exercised on real screens against the seeded server — plus the one table that
  does not (Activity), pinned as current behavior.
- A 401 opens exactly one in-app sign-in dialog.

Clock frozen before seeding, ``TZ=UTC``, offline weather, animations off.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from textual.widgets import DataTable, Static

from cli.test_contract_tui import SERVER_URL, make_seeded_app, make_tui, settle, wait_until
from cli.test_contract_tui_actuation import _record_toasts
from cli.test_tui import _text
from cli.tui_fixtures import _app_locks, tui_client_factory, writes
from golden import FROZEN_TS
from greenhouse_cli.client import IrrigationClient
from greenhouse_cli.tui.screens.activity import ActivityScreen
from greenhouse_cli.tui.screens.alerts import AlertsScreen
from greenhouse_cli.tui.screens.cluster import ClusterScreen
from greenhouse_cli.tui.screens.dashboard import DashboardScreen
from greenhouse_cli.tui.screens.modals import LoginScreen
from greenhouse_cli.tui.widgets import selected_key
from greenhouse_core.repository import IrrigationRepository
from server.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME, _make_stubbed_app

SIZE = (160, 50)
# Auto-refresh period for the G14 tests: short enough to keep them quick, long enough for a reload to finish.
REFRESH = 0.5


@pytest.fixture
def seeded(clean_env, frozen_clock):
    """(http, engine) for a seeded app — clock frozen before ``seed_greenhouse`` runs."""
    http, _, engine = make_seeded_app()
    yield http, engine
    engine.dispose()


def _run(coro) -> None:
    asyncio.run(coro)


# ── G13: blocking client calls never run on the event-loop thread ───────────


def _thread_recording_factory(http, calls: list[tuple[str, int]]):
    base = tui_client_factory(http)
    public = [n for n, _ in inspect.getmembers(IrrigationClient, inspect.isfunction) if not n.startswith("_")]

    def factory(token):
        client = base(token)
        for name in public:
            bound = getattr(client, name)

            def wrapper(*args, _bound=bound, _name=name, **kwargs):
                calls.append((_name, threading.get_ident()))
                return _bound(*args, **kwargs)

            setattr(client, name, wrapper)
        return client

    return factory


def test_client_calls_run_off_the_event_loop_thread(seeded):
    """G13: every IrrigationClient method call made while touring all screens ran in a worker thread."""
    http, _ = seeded
    calls: list[tuple[str, int]] = []
    loop_thread: list[int] = []

    async def scenario():
        loop_thread.append(threading.get_ident())
        tui = make_tui(http, factory=_thread_recording_factory(http, calls))
        async with tui.run_test(size=SIZE) as pilot:
            await settle(pilot, tui)
            for key in ("a", "l", "s", "o", "d"):
                await pilot.press(key)
                await settle(pilot, tui)
            await tui.push_screen(ClusterScreen(1))
            await settle(pilot, tui)
            for tab in ("tab-insights", "tab-plants"):
                tui.screen.query_one("TabbedContent").active = tab
                await settle(pilot, tui)
            await pilot.press("P")
            await settle(pilot, tui)
            await pilot.press("escape")
            await pilot.press("slash")
            await pilot.press(*"fern")
            await pilot.pause(0.4)
            await settle(pilot, tui)

    _run(scenario())
    methods = {name for name, _ in calls}
    assert {"list_clusters", "status", "list_alerts", "list_activity", "system_health", "insights", "search"} <= methods
    assert "sync_plants" in methods
    on_loop = sorted({name for name, ident in calls if ident == loop_thread[0]})
    assert on_loop == [], f"client methods ran on the event-loop thread: {on_loop}"


# ── G14: cursor stays on the same record across a real auto-refresh ─────────


async def _open_auto_refreshing(pilot, tui, key: str, screen_type: type) -> None:
    """Switch to ``screen_type`` with ``key`` once the dashboard has settled, with only that screen auto-refreshing.

    The app starts with ``refresh_seconds=0`` so the dashboard (the start screen, not under test) installs no
    timer: at a 0.5 s period a slow dashboard load overlaps the next tick's load and the two interleave in
    ``_render_cards`` (``NoMatches('#cluster-card-2')`` → ``WorkerFailed``). ``refresh_seconds`` is set before
    the key, so the target screen — mounted on its first ``switch_mode`` — installs the real ``set_interval``.
    """
    await settle(pilot, tui)
    tui.refresh_seconds = REFRESH
    await pilot.press(key)
    await wait_until(lambda: isinstance(tui.screen, screen_type), f"`{key}` to switch to {screen_type.__name__}")


def test_alerts_cursor_stays_on_record_across_auto_refresh(seeded):
    """G14: row k selected, a new alert appears server-side, the screen's timer reloads → same alert selected."""
    http, engine = seeded

    async def scenario():
        tui = make_tui(http)
        async with tui.run_test(size=SIZE) as pilot:
            await _open_auto_refreshing(pilot, tui, "a", AlertsScreen)
            screen = tui.screen
            assert isinstance(screen, AlertsScreen)
            table = screen.query_one("#alerts-table", DataTable)
            await wait_until(lambda: table.row_count == 2, "the first alerts load (2 open alerts)")
            table.move_cursor(row=1)
            chosen = selected_key(table)
            keys_before = [table.coordinate_to_cell_key((i, 0))[0].value for i in range(table.row_count)]
            with _app_locks[id(http)], Session(engine) as session:
                IrrigationRepository(session).upsert_alert(
                    "health:sensor:4",
                    "health",
                    "sensor_offline",
                    "Echeveria probe offline",
                    "No reading for 6h.",
                    severity="critical",
                    entity_type="sensor",
                    entity_id=4,
                    cluster_id=3,
                )
                session.commit()
            await wait_until(lambda: table.row_count == 3, "an auto-refresh showing the inserted alert")
            keys_after = [table.coordinate_to_cell_key((i, 0))[0].value for i in range(table.row_count)]
            assert selected_key(table) == chosen
            assert table.cursor_row == keys_after.index(chosen)
            # Pin today's ordering (newest id first at equal timestamps): the new alert lands on top, so the
            # selected record's row index moved from 1 to 2 while the key stayed.
            assert (keys_before, chosen) == (["2", "1"], "1")
            assert keys_after == ["3", "2", "1"]
            assert table.cursor_row == 2

    _run(scenario())


def test_plants_cursor_stays_on_record_across_refresh(seeded):
    """G14: plant row 1 selected; a plant that sorts above it is added server-side; ``r`` reloads the
    screen (the same ``reload`` the auto-refresh timer calls — a fast timer keeps cancelling the heavy cluster
    load before it finishes) → the same plant stays selected at its new index."""
    http, _ = seeded

    async def scenario():
        tui = make_tui(http)
        async with tui.run_test(size=SIZE) as pilot:
            await settle(pilot, tui)
            await tui.push_screen(ClusterScreen(1))
            await settle(pilot, tui)
            screen = tui.screen
            screen.query_one("TabbedContent").active = "tab-plants"
            await settle(pilot, tui)
            table = screen.query_one("#plants-table", DataTable)
            table.move_cursor(row=1)
            await settle(pilot, tui)
            assert selected_key(table) == "2"
            assert http.post("/api/v1/clusters/1/plants", json={"species": "Ficus lyrata"}).status_code == 201
            await pilot.press("r")
            await settle(pilot, tui)
            # Pin today's row order (plants come back sorted by species, so "Ficus" lands on top): the selected
            # record moved from row 1 to row 2 and the cursor followed it.
            assert [table.coordinate_to_cell_key((i, 0))[0].value for i in range(table.row_count)] == ["6", "1", "2"]
            assert selected_key(table) == "2"
            assert table.cursor_row == 2

    _run(scenario())


def test_activity_cursor_current_behavior_resets_to_top_on_refresh(seeded):
    """Pins current behavior: ActivityScreen.load clears and re-adds rows (no ``refill``), so an auto-refresh
    moves the cursor back to row 0 — the newest event — instead of keeping the selected record. Harmless today
    (activity rows have no row actions) but it is NOT the cursor-stability contract the other tables follow.
    """
    http, engine = seeded

    async def scenario():
        tui = make_tui(http)
        async with tui.run_test(size=SIZE) as pilot:
            await _open_auto_refreshing(pilot, tui, "l", ActivityScreen)
            screen = tui.screen
            assert isinstance(screen, ActivityScreen)
            table = screen.query_one("#activity-table", DataTable)
            await wait_until(lambda: table.row_count == 3, "the first activity load (3 events)")
            table.move_cursor(row=2)
            with _app_locks[id(http)], Session(engine) as session:
                IrrigationRepository(session).add_activity_event(
                    "sync", "system", "sync_completed", "fresh event", severity="info", timestamp=FROZEN_TS
                )
                session.commit()
            await wait_until(lambda: table.row_count == 4, "an auto-refresh showing the inserted event")
            assert table.cursor_row == 0
            assert table.get_row_at(0)[-1] == "fresh event"

    _run(scenario())


# ── 401 → in-app sign-in ────────────────────────────────────────────────────


def test_401_opens_one_sign_in_dialog_then_logs_in(clean_env, frozen_clock):
    """Concurrent 401s on startup open exactly one LoginScreen and raise no error toast; signing in posts the
    credentials verbatim, stores the token, toasts and reloads the dashboard."""
    application, engine = _make_stubbed_app(bypass_auth=False)
    lock = threading.Lock()
    log: list = []

    def factory(token):
        client = IrrigationClient(base_url=SERVER_URL, token=token or "")
        client.http = TestClient(application, raise_server_exceptions=False)
        if token:
            client.http.headers["Authorization"] = f"Bearer {token}"
        original = client._request

        def serialized(method, path, **kwargs):
            log.append((method, path, kwargs.get("json"), kwargs.get("params")))
            with lock:
                return original(method, path, **kwargs)

        client._request = serialized
        return client

    async def scenario():
        tui = make_tui(None, factory=factory)
        toasts = _record_toasts(tui)
        async with tui.run_test(size=SIZE) as pilot:
            await settle(pilot, tui)
            assert isinstance(tui.screen, LoginScreen)
            assert sum(isinstance(s, LoginScreen) for s in tui.screen_stack) == 1
            assert toasts == []
            startup = sorted({p for m, p, _, _ in log})
            assert startup == ["/api/v1/clusters", "/api/v1/health/system"]
            tui.screen.query_one("#username").value = TEST_ADMIN_USERNAME
            tui.screen.query_one("#password").value = TEST_ADMIN_PASSWORD
            await pilot.click("#login")
            await settle(pilot, tui)
            assert writes(log) == [
                ("POST", "/api/v1/auth/login", {"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
            ]
            assert toasts == [("information", f"Signed in as {TEST_ADMIN_USERNAME}")]
            assert isinstance(tui.screen, DashboardScreen)

    try:
        _run(scenario())
        assert (Path(clean_env) / "config" / "greenhouse" / "token").read_text()
    finally:
        engine.dispose()


def test_401_current_behavior_dashboard_says_cannot_reach_server(clean_env, frozen_clock):
    """Pins current (buggy) behavior: when the server answers 401, ``GreenhouseApp.api`` returns ``None`` and the
    dashboard renders "Cannot reach the server — press r to retry." behind the sign-in dialog, although the server
    was reached — see REFACTOR_NOTES.md."""
    application, engine = _make_stubbed_app(bypass_auth=False)

    def factory(token):
        client = IrrigationClient(base_url=SERVER_URL, token=token or "")
        client.http = TestClient(application, raise_server_exceptions=False)
        return client

    async def scenario():
        tui = make_tui(None, factory=factory)
        async with tui.run_test(size=SIZE) as pilot:
            await settle(pilot, tui)
            assert isinstance(tui.screen, LoginScreen)
            dashboard = tui.screen_stack[-2]
            assert isinstance(dashboard, DashboardScreen)
            assert "Cannot reach the server" in _text(dashboard.query_one("#dashboard-empty", Static))

    try:
        _run(scenario())
    finally:
        engine.dispose()
