"""Tests for the Textual TUI (``greenhouse tui``).

Pure helpers (sprites, formatting, summaries) are unit-tested directly. The
screens are driven with Textual's headless ``Pilot`` against the *real*
FastAPI app (in-memory SQLite + fake devices, seeded by ``tui_fixtures``), so
every client call the TUI makes is checked against the live route and schema.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import logging
import os
import re
import time
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Never, Protocol, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from rich.console import Console
from textual.app import App
from textual.geometry import Region
from textual.widgets import Checkbox, DataTable, Input, Static, TabbedContent
from textual.worker import WorkerCancelled
from typer.testing import CliRunner

from cli.tui_fixtures import seed_greenhouse, tui_client_factory, writes
from greenhouse_cli import tui as tui_pkg
from greenhouse_cli.client import IrrigationClient
from greenhouse_cli.main import app as cli_app
from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui import render, resources
from greenhouse_cli.tui.app import GreenhouseApp
from greenhouse_cli.tui.model import is_watering, sparkline_points, summarize
from greenhouse_cli.tui.screens import dashboard
from greenhouse_cli.tui.screens.activity import ActivityScreen
from greenhouse_cli.tui.screens.alerts import AlertsScreen
from greenhouse_cli.tui.screens.cluster import ClusterScreen
from greenhouse_cli.tui.screens.dashboard import DashboardScreen, _log_rebuild_failure
from greenhouse_cli.tui.screens.forms import Field, FormScreen, _display, parse_value
from greenhouse_cli.tui.screens.modals import ConfirmScreen, IrrigateScreen, LoginScreen, WaterNowScreen
from greenhouse_cli.tui.screens.search import SearchScreen
from greenhouse_cli.tui.screens.settings import SettingsScreen
from greenhouse_cli.tui.screens.system import SystemScreen
from greenhouse_cli.tui.sprites import (
    Mood,
    PixelSprite,
    logo_sprite,
    mood_for,
    normalize_category,
    plant_sprite,
    watering_can_sprite,
)
from greenhouse_cli.tui.table import DataTable as TuiDataTable
from greenhouse_cli.tui.widgets import ClusterCard, MetricChart, SpriteView, refill, selected_key
from server.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME, _make_stubbed_app

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Iterator

    from fastapi import FastAPI
    from textual.app import ComposeResult
    from textual.dom import DOMNode
    from textual.pilot import Pilot

    from cli.tui_fixtures import RequestLog
    from greenhouse_cli._client.transport import JSONObject
    from server.conftest import FakeDeviceWiring

    ClientFactory = Callable[[str | None], IrrigationClient]
    # A cluster-screen scenario body: (pilot, app, the open cluster screen).
    ClusterBody = Callable[[Pilot[None], GreenhouseApp, ClusterScreen], Awaitable[None]]

_IDLE_PASSES_REQUIRED = 2  # consecutive idle passes before ``_settle`` trusts the app is quiet

# Sprite geometry: plant sprites are 16 pixels wide and 16 tall (8 half-block lines).
PLANT_SPRITE_WIDTH = 16
PLANT_SPRITE_LINES = 8
WILTING_FRAMES = 3
SAMPLE_SPRITE_WIDTH = 3  # the ("AB.", "A.B") sample
THREE_ROWS_AS_LINES = 2  # three pixel rows pad to two half-block lines
GAUGE_WIDTH = 10

# The seeded greenhouse (``cli/tui_fixtures.py``): what each screen must show.
SEEDED_CLUSTERS = 3
LIVING_ROOM_PLANTS = 2
LIVING_ROOM_SENSORS = 2
LIVING_ROOM_HISTORY_ROWS = 3
SEEDED_OPEN_ALERTS = 2
SEEDED_ACTIVITY_ROWS = 3
SEEDED_DEVICES = 6
SEEDED_PUMPS = 2  # Living Room and Balcony; stop-all reaches both
DESK_CLUSTER_ID = 3
BALCONY_CLUSTER_ID = 2

# TestModel.STATUS: the driest plant and the averages it must summarise to.
STATUS_DRIEST_MOISTURE = 30.0
STATUS_MEAN_TEMPERATURE = 22.0
STATUS_NEWEST_READING_AT = 200
STATUS_IRRIGATOR_ID = 3

CHART_HOURS_AFTER_NEXT = 72  # ``]`` from the 24 h default
CHART_HOURS_SHORTEST = 6  # ``[`` pressed past the shortest range
FORM_INT_VALUE = 5
FORM_FLOAT_VALUE = 2.5
MAY_FIRST_2026_UTC = 1777593600  # 2026-05-01 00:00 UTC, the fallback when the zone is unknown
EDITED_WINDOW_END_HOUR = 10
EDITED_RESERVOIR_L = 12.5
EDITED_GLOBAL_INTERVAL_HOURS = 18
SCHEDULER_JOB_ROWS = 2  # duplicate pending job collapsed
MIN_AUTO_REFRESH_POLLS = 3
CLIENTS_MADE = 3  # session client, anonymous login client, signed-in client
POLLS_AFTER_RELOAD = 2
ACTIVITY_PAGE_SIZE = 100

SIZE = (160, 50)


class _Levelled(Protocol):
    """A widget carrying an ad-hoc numeric ``level`` attribute (what ``Widget.animate`` drives in the test)."""

    level: float


class _ValueField(Protocol):
    """A form field widget (``Input`` / ``Select`` / ``Checkbox``) whose ``value`` a test sets."""

    value: object


@pytest.fixture(autouse=True)
def _isolated_token_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("GREENHOUSE_API_TOKEN", raising=False)


@pytest.fixture
def greenhouse() -> Iterator[tuple[TestClient, FakeDeviceWiring]]:
    """(TestClient, fake-device wiring) for a seeded, auth-bypassed app."""
    application, engine = _make_stubbed_app(bypass_auth=True)
    http = TestClient(application, raise_server_exceptions=False)
    seed_greenhouse(http, engine)
    yield http, application.state.fake_devices
    engine.dispose()


def _app(http: TestClient, log: list[RequestLog] | None = None, refresh_seconds: float = 0) -> GreenhouseApp:
    return GreenhouseApp(
        "http://testserver", client_factory=tui_client_factory(http, log), refresh_seconds=refresh_seconds
    )


async def _settle(pilot: Pilot[None], tui: App[None], limit_s: float = 10.0) -> None:
    """Wait until no worker is running and the DOM has caught up.

    Workers chain (an action's worker starts a reload worker when it ends), so
    keep waiting until a pass finds nothing running — a fixed number of rounds
    is not enough on a slow CI runner. Exclusive workers legitimately cancel
    the run they supersede, so a cancelled worker is not a failure here.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit_s
    idle_passes = 0
    while idle_passes < _IDLE_PASSES_REQUIRED:
        await pilot.pause()
        with contextlib.suppress(WorkerCancelled):
            await tui.workers.wait_for_complete()
        busy = any(w.is_running for w in tui.workers)
        idle_passes = 0 if busy else idle_passes + 1
        if loop.time() > deadline:
            msg = f"workers still running after {limit_s}s: {list(tui.workers)}"
            raise AssertionError(msg)
    await pilot.pause()


def _run(coro: Coroutine[object, object, None]) -> None:
    asyncio.run(coro)


async def _settle_until(pilot: Pilot[None], tui: App[None], done: Callable[[], object], limit_s: float = 30.0) -> None:
    """Settle repeatedly until ``done()`` holds.

    A dismissed dialog hands its result to a callback that starts the next worker one
    message later, which a single settle can miss on a slow runner.
    """
    deadline = asyncio.get_running_loop().time() + limit_s
    while True:
        await _settle(pilot, tui)
        if done() or asyncio.get_running_loop().time() > deadline:
            return


def _screen_text(tui: GreenhouseApp) -> str:
    """Every Static on the active screen, joined — the failure message shows what the user saw."""
    return " | ".join(_text(w) for w in tui.screen.query(Static))


def _text(widget: Static) -> str:
    """Plain text of whatever a Static widget is currently showing."""
    return _render(widget.content, width=200)


def _render(renderable: object, width: int = 40) -> str:
    console = Console(width=width, color_system=None, record=True)
    console.print(renderable)
    return console.export_text()


# ── Sprites ─────────────────────────────────────────────────────────────────


class TestSprites:
    """Pure sprite logic: moods, half-block rendering, categories and animation frames."""

    @pytest.mark.parametrize(
        ("moisture", "expected"),
        [
            (None, Mood.UNKNOWN),
            (20.0, Mood.WILTING),
            (35.0, Mood.THIRSTY),
            (45.0, Mood.OK),
            (60.0, Mood.THRIVING),
            (90.0, Mood.SOAKED),
        ],
    )
    def test_mood_with_fallback_band(self, moisture: float | None, expected: Mood) -> None:
        """Without a care band, the fallback thresholds map moisture to the documented mood."""
        assert mood_for(moisture) is expected

    def test_mood_uses_supplied_band(self) -> None:
        """A plant's own moisture band overrides the fallback, so the same reading can be thirsty or soaked."""
        assert mood_for(50.0, band_min=55.0, band_max=80.0) is Mood.THIRSTY
        assert mood_for(50.0, band_min=20.0, band_max=40.0) is Mood.SOAKED

    def test_half_block_rendering(self) -> None:
        """Two pixel rows collapse into one half-block line, with transparency kept per half."""
        sprite = PixelSprite(("AB.", "A.B"), {"A": "#ff0000", "B": "#00ff00"})
        assert sprite.width == SAMPLE_SPRITE_WIDTH
        assert sprite.height == 1
        # A over A → full ▀ ; B over transparent → ▀ ; transparent over B → ▄
        assert _render(sprite).splitlines()[0] == "▀▀▄"

    def test_odd_row_count_is_padded(self) -> None:
        """An odd pixel-row count is padded so the last half-block line still renders."""
        sprite = PixelSprite(("A", "A", "A"), {"A": "#ffffff"})
        assert sprite.height == THREE_ROWS_AS_LINES
        assert _render(sprite).splitlines()[:2] == ["▀", "▀"]

    @pytest.mark.parametrize("category", ["tropical", "fern", "succulent", "cacti", "fruit_tree", None, "mystery"])
    def test_every_category_draws_a_potted_plant(self, category: str | None) -> None:
        """Every category has a full-size sprite whose bottom row (the pot) is painted."""
        sprite = plant_sprite(category, Mood.OK)
        assert sprite.width == PLANT_SPRITE_WIDTH
        assert sprite.height == PLANT_SPRITE_LINES
        assert sprite.rows[-1].strip(".")  # pot base is painted

    def test_plants_animate(self) -> None:
        """Successive frames differ, so an animated plant visibly moves."""
        frames = {plant_sprite("tropical", Mood.OK, f).rows for f in range(4)}
        assert len(frames) > 1

    def test_thriving_sparkles_and_wilting_drops_leaves(self) -> None:
        """Mood-specific effects: sparkles when thriving, a leaf that falls frame by frame when wilting."""
        assert any("K" in r for r in plant_sprite("fern", Mood.THRIVING, 0).rows)
        wilting = [plant_sprite("fern", Mood.WILTING, f).rows for f in range(3)]
        assert len(set(wilting)) == WILTING_FRAMES  # the falling leaf moves every frame

    def test_watering_overlays_drops(self) -> None:
        """Water drops appear only while the plant is being watered."""
        assert any("W" in r for r in plant_sprite("cacti", Mood.THIRSTY, 1, watering=True).rows)
        assert not any("W" in r for r in plant_sprite("cacti", Mood.THIRSTY, 1).rows)

    def test_mood_recolours_foliage(self) -> None:
        """Foliage colour reflects the mood, so a wilting plant reads differently at a glance."""
        assert plant_sprite("fern", Mood.THRIVING).palette["G"] != plant_sprite("fern", Mood.WILTING).palette["G"]

    def test_category_aliases(self) -> None:
        """Free-form plant categories normalise to the sprite set, with a generic fallback."""
        assert normalize_category("Cactus") == "cacti"
        assert normalize_category("fruit-tree") == "fruit_tree"
        assert normalize_category(None) == "generic"

    def test_misc_sprites(self) -> None:
        """The logo renders and the watering can has a distinct pouring frame."""
        assert logo_sprite().height > 0
        assert watering_can_sprite(pouring=True, frame=1).rows != watering_can_sprite().rows


# ── Formatting / model ──────────────────────────────────────────────────────


class TestFormatting:
    """Formatting helpers shared by every TUI screen."""

    def test_ago(self) -> None:
        """Relative timestamps cover never, seconds, hours and the future."""
        assert fmt.ago(None) == "never"
        assert fmt.ago(1000, reference=1030) == "30s ago"
        assert fmt.ago(1000, reference=1000 + 7260) == "2h01m ago"
        assert fmt.ago(1000 + 3 * 86400, reference=1000) == "in 3d0h"

    def test_age(self) -> None:
        """Ages in seconds render as a compact 'ago' string, with a dash for unknown."""
        assert fmt.age(None) == "—"
        assert fmt.age(0) == "0s ago"
        assert fmt.age(300) == "5m ago"

    def test_num(self) -> None:
        """Numbers get a unit and a configurable precision, with a dash for missing values."""
        assert fmt.num(None, "%") == "—"
        assert fmt.num(42.123, "%") == "42.1%"
        assert fmt.num(42.6, "%", 0) == "43%"

    def test_bar(self) -> None:
        """Gauges have a fixed width and fill in proportion to the value."""
        assert len(fmt.bar(None, GAUGE_WIDTH)) == GAUGE_WIDTH
        gauge = fmt.bar(50, GAUGE_WIDTH, 40, 70)
        assert gauge.plain.startswith("█████")
        assert len(gauge) == GAUGE_WIDTH

    def test_weekday_mask(self) -> None:
        """Window weekday bitmasks render as 'every day' or a one-letter-per-day strip."""
        assert fmt.weekday_mask(127) == "every day"
        assert fmt.weekday_mask(1 | 4) == "M·W····"


class TestModel:
    """Payload-to-summary view model used by the dashboard and cluster screens."""

    STATUS: ClassVar[dict[str, Any]] = {
        "cluster": {"id": 7, "name": "Shelf", "environment": "indoor", "location": None},
        "plants": [
            {"id": 1, "species": "A", "category": "fern"},
            {"id": 2, "species": "B", "category": "cacti"},
        ],
        "sensors": [
            {"plant_id": 1, "last_reading": {"timestamp": 100, "soil_moisture": 50.0, "temperature": 20.0}},
            {"plant_id": 2, "last_reading": {"timestamp": 200, "soil_moisture": 30.0, "temperature": 24.0}},
            {"plant_id": None, "last_reading": None},
        ],
        "irrigator": {"id": 3, "name": "pump", "last_event": None},
        "decision": {"action": "irrigate"},
    }

    def test_summarize_picks_driest_plant(self) -> None:
        """The cluster summary is driven by the driest plant, like the engine (invariant 2)."""
        s = summarize(self.STATUS, {"threshold": {"min": 40, "max": 70}, "datasets": []})
        assert s.min_moisture == STATUS_DRIEST_MOISTURE
        assert s.driest is not None
        assert s.driest.species == "B"
        assert s.temperature == STATUS_MEAN_TEMPERATURE
        assert s.newest_reading_at == STATUS_NEWEST_READING_AT
        assert s.mood is Mood.THIRSTY
        assert [p.mood for p in s.plants] == [Mood.OK, Mood.THIRSTY]
        assert s.irrigator_id == STATUS_IRRIGATOR_ID
        assert not s.watering

    def test_sparkline_uses_per_timestamp_minimum(self) -> None:
        """The sparkline plots the per-timestamp minimum across sensors, and is empty without data."""
        chart = {"datasets": [{"points": [[1, 50], [2, 40]]}, {"points": [[1, 45], [3, 60]]}]}
        assert sparkline_points(chart) == [45, 40, 60]
        assert sparkline_points(None) == []

    def test_is_watering(self) -> None:
        """Watering is inferred from a start event still inside its duration."""
        event = {"action": "start", "timestamp": 1000, "duration_minutes": 5}
        assert is_watering(event, reference=1200)
        assert not is_watering(event, reference=1000 + 301)
        assert not is_watering({**event, "action": "stop"}, reference=1001)
        assert not is_watering(None)


# ── Screens (full stack) ────────────────────────────────────────────────────


class TestDashboard:
    """Dashboard screen against the seeded stub server."""

    def test_cards_for_every_cluster(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Every cluster gets a card summarising its plants and state."""
        http, _ = greenhouse

        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                assert isinstance(tui.screen, DashboardScreen)
                cards = list(tui.screen.query(ClusterCard))
                assert sorted(c.summary.name for c in cards) == ["Balcony", "Desk", "Living Room"]
                balcony = next(c.summary for c in cards if c.summary.name == "Balcony")
                assert balcony.mood is Mood.WILTING
                assert balcony.sparkline
                desk = next(c.summary for c in cards if c.summary.name == "Desk")
                assert desk.irrigator_id is None
                # A card has focus so Enter opens it.
                assert isinstance(tui.focused, ClusterCard)
                await pilot.press("enter")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, ClusterScreen)
                await pilot.press("escape")
                await pilot.pause()
                assert isinstance(tui.screen, DashboardScreen)

        _run(scenario())

    def test_rapid_reloads_never_duplicate_cards(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Regression: overlapping reloads never mount two cards with the same ID.

        They raced remove/mount and raised MountError when a superseded worker was
        cancelled mid-render.
        """
        http, _ = greenhouse

        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                assert isinstance(tui.screen, DashboardScreen)
                for _ in range(12):
                    tui.screen.reload()
                    await pilot.pause(0.01)
                await _settle(pilot, tui)
                ids = [str(c.id) for c in tui.screen.query(ClusterCard)]
                assert sorted(ids) == ["cluster-card-1", "cluster-card-2", "cluster-card-3"]

        _run(scenario())

    def test_stop_all_requires_confirmation(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Stop-all is guarded by a confirmation dialog before any device call."""
        http, wiring = greenhouse

        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await pilot.press("X")
                await pilot.pause()
                assert isinstance(tui.screen, ConfirmScreen)
                await pilot.press("n")
                await _settle(pilot, tui)
                assert not [c for c in wiring.irrigator.calls if c[0] == "stop"]
                await pilot.press("X")
                await pilot.pause()
                await pilot.press("y")
                await _settle(pilot, tui)
                assert len([c for c in wiring.irrigator.calls if c[0] == "stop"]) == SEEDED_PUMPS

        _run(scenario())

    def test_unreachable_server(self) -> None:
        """A refused connection is reported on screen instead of crashing the app."""

        def refuse(_request: httpx.Request) -> Never:
            msg = "refused"
            raise httpx.ConnectError(msg)

        def factory(_token: str | None) -> IrrigationClient:
            return IrrigationClient(base_url="http://down", token="", transport=httpx.MockTransport(refuse))

        async def scenario() -> None:
            tui = GreenhouseApp("http://down", client_factory=factory, refresh_seconds=0)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                empty = tui.screen.query_one("#dashboard-empty", Static)
                assert "Cannot reach the server" in _text(empty)

        _run(scenario())


class TestClusterScreen:
    """Cluster detail screen: tabs, charts and actuation dialogs."""

    def _open(self, http: TestClient, cluster_id: int, body: ClusterBody) -> None:
        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await tui.push_screen(ClusterScreen(cluster_id))
                await _settle(pilot, tui)
                screen = tui.screen
                assert isinstance(screen, ClusterScreen), screen
                await body(pilot, tui, screen)

        _run(scenario())

    def test_tabs_are_populated(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Opening a cluster fills every tab from the API payloads."""
        http, _ = greenhouse

        async def body(_pilot: Pilot[None], _tui: GreenhouseApp, screen: ClusterScreen) -> None:
            assert screen.summary is not None
            assert screen.summary.name == "Living Room"
            assert len(screen.query("PlantTile")) == LIVING_ROOM_PLANTS
            assert screen.query_one("#sensors-table", DataTable).row_count == LIVING_ROOM_SENSORS
            assert screen.query_one("#plants-table", DataTable).row_count == LIVING_ROOM_PLANTS
            assert screen.query_one("#history-table", DataTable).row_count == LIVING_ROOM_HISTORY_ROWS
            decision = _text(screen.query_one("#decision-panel", Static))
            assert "Decision engine" in decision
            config = _text(screen.query_one("#config-panel", Static))
            assert "duration_minutes" in config
            assert screen.query_one("#windows-table", DataTable).row_count == 0

        self._open(http, 1, body)

    def test_metric_and_range_cycle(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """The chart metric and range keys cycle through their options and reload the chart."""
        http, _ = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            screen.query_one("TabbedContent", TabbedContent).active = "tab-charts"
            await pilot.pause()
            await pilot.press("m")
            await _settle(pilot, tui)
            assert screen.metric == "temperature"
            await pilot.press("]")
            await _settle(pilot, tui)
            assert screen.hours == CHART_HOURS_AFTER_NEXT
            await pilot.press("[", "[", "[")
            await _settle(pilot, tui)
            assert screen.hours == CHART_HOURS_SHORTEST
            assert "Temperature" in _text(screen.query_one("#chart-hint", Static))
            assert isinstance(screen.query_one("#metric-chart"), MetricChart)

        self._open(http, 1, body)

    def test_irrigate_dry_run_does_not_actuate(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """The irrigate dialog defaults to a dry run, which never reaches the device."""
        http, wiring = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, _screen: ClusterScreen) -> None:
            await pilot.press("i")
            await pilot.pause()
            assert isinstance(tui.screen, IrrigateScreen)
            assert tui.screen.query_one("#dry-run", Checkbox).value is True
            await pilot.click("#run")
            await _settle(pilot, tui)
            assert not [c for c in wiring.irrigator.calls if c[0] == "start"]

        self._open(http, 1, body)

    def test_water_now_and_stop(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Water-now starts the pump for the chosen minutes and stop turns it off again."""
        http, wiring = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            assert screen.summary is not None
            irrigator_id = screen.summary.irrigator_id
            await pilot.press("w")
            await pilot.pause()
            assert isinstance(tui.screen, WaterNowScreen)
            tui.screen.query_one("#minutes", Input).value = "2"
            await pilot.click("#start")
            await _settle(pilot, tui)
            assert ("start", irrigator_id, 2) in wiring.irrigator.calls

            await pilot.press("x")
            await pilot.pause()
            assert isinstance(tui.screen, ConfirmScreen)
            await pilot.click("#confirm")
            await _settle(pilot, tui)
            assert ("stop", irrigator_id) in wiring.irrigator.calls

        self._open(http, 1, body)

    def test_sensor_only_cluster_has_no_irrigator_actions(
        self, greenhouse: tuple[TestClient, FakeDeviceWiring]
    ) -> None:
        """A cluster without a pump ignores the water-now key instead of calling a device."""
        http, wiring = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            assert screen.summary is not None
            assert screen.summary.irrigator_id is None
            await pilot.press("w")
            await pilot.pause()
            assert isinstance(tui.screen, ClusterScreen)
            assert not wiring.irrigator.calls

        self._open(http, 3, body)


class TestOtherScreens:
    """Alerts, activity and system screens."""

    def test_alerts_acknowledge_and_filter(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Alerts can be acknowledged from the table and filtered by state."""
        http, _ = greenhouse

        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("a")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, AlertsScreen)
                table = tui.screen.query_one(DataTable)
                assert table.row_count == SEEDED_OPEN_ALERTS
                await pilot.press("k")
                await _settle(pilot, tui)
                assert table.row_count == 1
                assert http.get("/api/v1/alerts", params={"status": "acknowledged"}).json()["items"]
                await pilot.press("f")
                await _settle(pilot, tui)
                assert tui.screen.status_filter == "acknowledged"
                assert table.row_count == 1

        _run(scenario())

    def test_activity_feed(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """The activity screen lists the seeded activity rows."""
        http, _ = greenhouse

        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("l")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, ActivityScreen)
                assert tui.screen.query_one(DataTable).row_count == SEEDED_ACTIVITY_ROWS
                await pilot.press("f")
                await _settle(pilot, tui)
                assert tui.screen.severity == "warning"
                assert tui.screen.query_one(DataTable).row_count == 1

        _run(scenario())

    def test_system_screen(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """The system screen shows device health and scheduler state."""
        http, _ = greenhouse

        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("s")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, SystemScreen)
                assert tui.screen.query_one("#devices-table", DataTable).row_count == SEEDED_DEVICES
                assert tui.screen.query_one("#jobs-table", DataTable).row_count >= 1
                assert "automatic runs" in _text(tui.screen.query_one("#scheduler-panel", Static))

        _run(scenario())


def _token_client_factory(application: FastAPI) -> ClientFactory:
    """Client factory on the auth-enforcing app that sends the stored token, if any."""

    def factory(token: str | None) -> IrrigationClient:
        client = IrrigationClient(base_url="http://testserver", token=token or "")
        client.http = TestClient(application, raise_server_exceptions=False)
        if token:
            client.http.headers["Authorization"] = f"Bearer {token}"
        return client

    return factory


class TestLogin:
    """Login flow when the server requires authentication."""

    def test_401_prompts_login_and_stores_token(self) -> None:
        """A 401 opens the login dialog and a successful login stores the token for next time."""
        application, engine = _make_stubbed_app(bypass_auth=False)
        http = TestClient(application, raise_server_exceptions=False)

        async def scenario() -> None:
            tui = GreenhouseApp(
                "http://testserver", client_factory=_token_client_factory(application), refresh_seconds=0
            )
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                assert isinstance(tui.screen, LoginScreen)
                tui.screen.query_one("#username", Input).value = TEST_ADMIN_USERNAME
                tui.screen.query_one("#password", Input).value = TEST_ADMIN_PASSWORD
                await pilot.click("#login")
                await _settle_until(pilot, tui, lambda: not isinstance(tui.screen, LoginScreen))
                assert isinstance(tui.screen, DashboardScreen), _screen_text(tui)
                assert "Cannot reach" not in _text(tui.screen.query_one("#dashboard-empty", Static))

        try:
            assert http.get("/api/v1/clusters").status_code == HTTPStatus.UNAUTHORIZED
            _run(scenario())
            token_path = Path(os.environ["XDG_CONFIG_HOME"]) / "greenhouse" / "token"
            assert token_path.read_text()
        finally:
            engine.dispose()


# ── CLI wiring ──────────────────────────────────────────────────────────────


class TestTuiCommand:
    """The ``greenhouse tui`` Typer command."""

    def test_launches_with_resolved_server(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The command passes the resolved server, refresh period and animation flag to the app."""
        seen: dict[str, object] = {}
        monkeypatch.setattr(
            "greenhouse_cli.tui.run",
            lambda server, refresh_seconds, animations: seen.update(
                server=server, refresh=refresh_seconds, animations=animations
            ),
        )
        result = CliRunner().invoke(
            cli_app, ["--server", "http://10.0.0.5:8000", "tui", "--refresh", "10", "--no-animation"]
        )
        assert result.exit_code == 0, result.output
        assert seen == {"server": "http://10.0.0.5:8000", "refresh": 10.0, "animations": False}

    def test_env_server_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without --server, the server URL falls back to IRRIGATION_SERVER_URL."""
        seen: dict[str, object] = {}
        monkeypatch.setenv("IRRIGATION_SERVER_URL", "http://192.0.2.10:8000")
        monkeypatch.setattr("greenhouse_cli.tui.run", lambda server, **_kw: seen.update(server=server))
        result = CliRunner().invoke(cli_app, ["tui"])
        assert result.exit_code == 0, result.output
        assert seen["server"] == "http://192.0.2.10:8000"


# ── Feature coverage: CRUD, insights, settings, search, maintenance ─────────

# Read endpoints the TUI deliberately doesn't call because a richer payload it
# already loads carries the same data. Everything else on IrrigationClient must
# be reachable from some screen — that's what "the TUI covers every feature" means.
REDUNDANT_READS = {
    "get_alert": "alert list rows carry the full alert",
    "get_cluster": "GET /clusters/{id}/detail",
    "get_config": "effective config + /detail",
    "get_irrigator": "/detail inlines the irrigator",
    "list_irrigators": "per-cluster /status",
    "list_plants": "/status inlines plants",
    "list_sensors": "/status inlines sensors",
    "list_windows": "/detail inlines windows",
    "health": "/health/system supersedes it",
}


def test_tui_reaches_every_client_capability() -> None:
    """Every public client method is reachable from the TUI, so the TUI never lags behind the CLI."""
    src = "\n".join(p.read_text() for p in Path(tui_pkg.__file__).parent.rglob("*.py"))
    public = [n for n, _ in inspect.getmembers(IrrigationClient, inspect.isfunction) if not n.startswith("_")]
    missing = [n for n in public if n not in REDUNDANT_READS and not re.search(rf"\.{n}\b", src)]
    assert not missing, f"IrrigationClient methods with no TUI entry point: {missing}"


async def _add_ficus(pilot: Pilot[None], tui: GreenhouseApp, http: TestClient) -> dict[str, object]:
    """Add a Ficus through the plants tab's new-plant form; return it as the API lists it."""
    await pilot.press("n")
    await pilot.pause()
    await _submit_form(pilot, tui, species="Ficus lyrata", category="tropical", water_needs="medium")
    plants = http.get("/api/v1/clusters/1/plants").json()
    ficus: dict[str, object] = next(p for p in plants if p["species"] == "Ficus lyrata")
    assert ficus["category"] == "tropical"
    return ficus


def _fill(screen: DOMNode, **values: object) -> None:
    """Set FormScreen inputs by field name."""
    for name, value in values.items():
        cast("_ValueField", screen.query_one(f"#field-{name}")).value = value


async def _submit_form(pilot: Pilot[None], tui: GreenhouseApp, **values: object) -> None:
    assert isinstance(tui.screen, FormScreen), tui.screen
    _fill(tui.screen, **values)
    await pilot.click("#submit")
    await _settle(pilot, tui)


async def _confirm(pilot: Pilot[None], tui: GreenhouseApp) -> None:
    assert isinstance(tui.screen, ConfirmScreen), tui.screen
    await pilot.click("#confirm")
    await _settle(pilot, tui)


class TestForms:
    """Form field parsing and the form dialogs' validation behaviour."""

    def test_parse_values(self) -> None:
        """Each field kind parses its raw input into the typed value the API expects."""
        assert parse_value(Field("a", "A", "int"), " 5 ") == FORM_INT_VALUE
        assert parse_value(Field("a", "A", "float"), "2.5") == FORM_FLOAT_VALUE
        assert parse_value(Field("a", "A"), "  ") is None
        assert parse_value(Field("a", "A", "bool"), raw=True) is True
        assert parse_value(Field("a", "A", "json"), '{"x": 1}') == {"x": 1}
        assert isinstance(parse_value(Field("a", "A", "datetime"), "2026-10-01 08:30"), int)

    def test_datetime_fields_use_the_timezone_preference(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """D8: vacation datetimes are read and shown in the server's ``timezone`` preference, not the machine's zone."""
        monkeypatch.setenv("TZ", "America/New_York")
        time.tzset()
        try:
            rome_midnight = 1777586400  # 2026-05-01 00:00 Europe/Rome (18:00 the day before in New York)
            starts, ends, *_ = resources.vacation_fields(
                {"starts_at": rome_midnight, "ends_at": rome_midnight}, "Europe/Rome"
            )
            assert parse_value(starts, "2026-05-01 00:00") == rome_midnight
            assert _display(starts) == _display(ends) == "2026-05-01 00:00"
            ((_, cells),) = render.vacation_rows(
                [{"id": 1, "starts_at": rome_midnight, "ends_at": rome_midnight}], None, "Europe/Rome"
            )
            assert cells[1:3] == ["2026-05-01 00:00", "2026-05-01 00:00"]
            # No preference (or an unknown zone): UTC, never the machine's local zone.
            assert parse_value(Field("s", "Starts", "datetime"), "2026-05-01 00:00") == MAY_FIRST_2026_UTC
            assert (
                parse_value(Field("s", "Starts", "datetime", tz="Not/AZone"), "2026-05-01 00:00") == MAY_FIRST_2026_UTC
            )
        finally:
            monkeypatch.undo()
            time.tzset()

    @pytest.mark.parametrize(
        ("field", "raw", "message"),
        [
            (Field("a", "Name", required=True), "", "Name is required"),
            (Field("a", "Minutes", "int"), "abc", "whole number"),
            (Field("a", "Starts", "datetime"), "tomorrow", "YYYY-MM-DD HH:MM"),
            (Field("a", "Config", "json"), "[1]", "JSON object"),
        ],
    )
    def test_parse_errors(self, field: Field, raw: str, message: str) -> None:
        """Invalid input raises a ValueError whose message tells the user what is wrong."""
        with pytest.raises(ValueError, match=message):
            parse_value(field, raw)

    def test_invalid_form_stays_open(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """A form with invalid input stays open instead of sending a request."""
        http, _ = greenhouse

        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await pilot.press("n")
                await pilot.pause()
                _fill(tui.screen, name="")
                await pilot.click("#submit")
                await pilot.pause()
                assert isinstance(tui.screen, FormScreen)
                assert "Name is required" in str(tui.screen.query_one("#form-error").render())

        _run(scenario())


class TestClusterCrud:
    """Create / edit / delete flows on the cluster screen, checked against the server state."""

    def _open(self, http: TestClient, cluster_id: int, body: ClusterBody, tab: str | None = None) -> None:
        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await tui.push_screen(ClusterScreen(cluster_id))
                await _settle(pilot, tui)
                if tab:
                    tui.screen.query_one("TabbedContent", TabbedContent).active = tab
                    await _settle(pilot, tui)
                screen = tui.screen
                assert isinstance(screen, ClusterScreen), screen
                await body(pilot, tui, screen)

        _run(scenario())

    def test_create_cluster_from_dashboard(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """The dashboard's new-cluster form creates the cluster on the server."""
        http, _ = greenhouse

        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await pilot.press("n")
                await pilot.pause()
                await _submit_form(pilot, tui, name="Kitchen", location="sill", environment="indoor")
                assert "Kitchen" in [c["name"] for c in http.get("/api/v1/clusters").json()]
                assert len(tui.screen.query(ClusterCard)) == SEEDED_CLUSTERS + 1

        _run(scenario())

    def test_plant_add_edit_move_delete(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """A plant can be added, edited, moved to another cluster and deleted from the plants tab."""
        http, _ = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            ficus = await _add_ficus(pilot, tui, http)
            table = screen.query_one("#plants-table", DataTable)
            table.move_cursor(row=table.get_row_index(str(ficus["id"])))
            await pilot.press("u")
            await pilot.pause()
            await _submit_form(pilot, tui, notes="by the window")
            assert http.get(f"/api/v1/plants/{ficus['id']}").json()["notes"] == "by the window"

            await pilot.press("M")
            await _settle(pilot, tui)
            await _submit_form(pilot, tui, target_cluster_id=3)
            assert http.get(f"/api/v1/plants/{ficus['id']}").json()["cluster_id"] == DESK_CLUSTER_ID

            table.move_cursor(row=0)
            first_key = table.coordinate_to_cell_key(table.cursor_coordinate)[0].value
            assert first_key is not None
            first = int(first_key)
            await pilot.press("delete")
            await pilot.pause()
            await _confirm(pilot, tui)
            assert http.get(f"/api/v1/plants/{first}").status_code == HTTPStatus.NOT_FOUND

        self._open(http, 1, body, tab="tab-plants")

    def test_plant_chart_toggle_and_plant_db_sync(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """The plant chart toggles independently of the cluster chart, and P syncs the plant DB."""
        http, _ = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            assert screen.plant_chart == "health"
            await pilot.press("m")
            await _settle(pilot, tui)
            assert screen.plant_chart == "moisture"
            assert screen.metric == "soil_moisture"  # cluster chart untouched
            await pilot.press("P")
            await _settle(pilot, tui)

        self._open(http, 1, body, tab="tab-plants")

    def test_sensor_add_and_delete(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """A sensor can be registered and deleted from the sensors tab."""
        http, _ = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            await pilot.press("n")
            await pilot.pause()
            await _submit_form(
                pilot, tui, tuya_device_id="fake_tuya_device_aabbccdd", name="Spare probe", type="tuya.tr301z"
            )
            sensors = http.get("/api/v1/clusters/1/sensors").json()
            spare = next(s for s in sensors if s["name"] == "Spare probe")
            table = screen.query_one("#sensors-table", DataTable)
            table.move_cursor(row=table.get_row_index(str(spare["id"])))
            await pilot.press("u")
            await pilot.pause()
            await _submit_form(pilot, tui, name="Renamed probe")
            assert http.get(f"/api/v1/clusters/1/sensors/{spare['id']}").json()["name"] == "Renamed probe"
            await pilot.press("delete")
            await pilot.pause()
            await _confirm(pilot, tui)
            assert http.get(f"/api/v1/clusters/1/sensors/{spare['id']}").status_code == HTTPStatus.NOT_FOUND

        self._open(http, 1, body, tab="tab-sensors")

    def test_windows_crud(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Irrigation windows can be created, edited and deleted from the windows tab."""
        http, _ = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            await pilot.press("n")
            await pilot.pause()
            await _submit_form(pilot, tui, start_hour="6", end_hour="9", label="morning")
            windows = http.get("/api/v1/clusters/1/windows").json()["windows"]
            assert [(w["start_hour"], w["end_hour"], w["weekday_mask"]) for w in windows] == [(6, 9, 127)]
            assert screen.query_one("#windows-table", DataTable).row_count == 1
            await pilot.press("u")
            await pilot.pause()
            await _submit_form(pilot, tui, end_hour="10")
            assert http.get("/api/v1/clusters/1/windows").json()["windows"][0]["end_hour"] == EDITED_WINDOW_END_HOUR
            await pilot.press("delete")
            await pilot.pause()
            await _confirm(pilot, tui)
            assert http.get("/api/v1/clusters/1/windows").json()["windows"] == []

        self._open(http, 1, body, tab="tab-windows")

    def test_config_edit(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Editing the cluster config writes the new values and refreshes the config panel."""
        http, _ = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            await pilot.press("u")
            await pilot.pause()
            await _submit_form(pilot, tui, duration_minutes="7", quiet_start_hour="22", quiet_end_hour="6")
            config = http.get("/api/v1/clusters/1/config").json()
            assert (config["duration_minutes"], config["quiet_start_hour"], config["quiet_end_hour"]) == (7, 22, 6)
            assert "7" in _text(screen.query_one("#config-panel", Static))

        self._open(http, 1, body, tab="tab-config")

    def test_irrigator_edit_detach_attach(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """The pump can be edited, detached and attached again."""
        http, _ = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            assert screen.summary is not None
            await pilot.press("u")
            await pilot.pause()
            await _submit_form(pilot, tui, reservoir_l="12.5", config='{"version": "3.5"}')
            irrigator = http.get("/api/v1/clusters/1/irrigator").json()
            assert irrigator["reservoir_l"] == EDITED_RESERVOIR_L
            assert irrigator["config"] == {"version": "3.5"}

            await pilot.press("delete")
            await pilot.pause()
            await _confirm(pilot, tui)
            assert http.get("/api/v1/clusters/1/irrigator").status_code == HTTPStatus.NOT_FOUND
            assert screen.summary.irrigator_id is None

            await pilot.press("n")
            await pilot.pause()
            await _submit_form(
                pilot, tui, tuya_device_id="fake_tuya_device_00112233", name="New pump", type="rainpoint.ik10pw"
            )
            assert http.get("/api/v1/clusters/1/irrigator").json()["name"] == "New pump"

        self._open(http, 1, body)

    def test_log_manual(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Logging a manual watering adds a history row without actuating the pump."""
        http, wiring = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            before = screen.query_one("#history-table", DataTable).row_count
            await pilot.press("L")
            await pilot.pause()
            await _submit_form(pilot, tui, minutes="4", notes="watering can")
            assert screen.query_one("#history-table", DataTable).row_count == before + 1
            assert not [c for c in wiring.irrigator.calls if c[0] == "start"]  # logging never actuates

        self._open(http, 1, body, tab="tab-history")

    def test_edit_and_delete_cluster(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """The cluster can be renamed and, after confirmation, deleted."""
        http, _ = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, _screen: ClusterScreen) -> None:
            await pilot.press("e")
            await pilot.pause()
            await _submit_form(pilot, tui, name="Desk (office)")
            assert http.get("/api/v1/clusters/3").json()["name"] == "Desk (office)"
            await pilot.press("D")
            await pilot.pause()
            await _confirm(pilot, tui)
            assert http.get("/api/v1/clusters/3").status_code == HTTPStatus.NOT_FOUND
            assert isinstance(tui.screen, DashboardScreen)

        self._open(http, 3, body)

    def test_insights_tab(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """The insights tab shows care insights, stats and the learning report."""
        http, _ = greenhouse

        async def body(_pilot: Pilot[None], _tui: GreenhouseApp, screen: ClusterScreen) -> None:
            assert "Care insights" in _text(screen.query_one("#insights-panel", Static))
            assert "Needs water" in _text(screen.query_one("#insights-panel", Static))
            assert "events" in _text(screen.query_one("#stats-panel", Static))
            assert "Learning report" in _text(screen.query_one("#learn-panel", Static))

        self._open(http, 1, body, tab="tab-insights")

    def test_export_stats_csv(
        self, greenhouse: tuple[TestClient, FakeDeviceWiring], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The export key writes the stats CSV to the working directory."""
        http, _ = greenhouse
        monkeypatch.chdir(tmp_path)

        async def body(pilot: Pilot[None], tui: GreenhouseApp, _screen: ClusterScreen) -> None:
            await pilot.press("E")
            await _settle(pilot, tui)
            exported = tmp_path / "greenhouse-living-room-stats-30d.csv"
            assert exported.exists()
            assert exported.read_text()

        self._open(http, 1, body)

    def test_overlay_metric(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Cycling the metric reaches the overlay chart with its hint."""
        http, _ = greenhouse

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            for _ in range(4):
                await pilot.press("m")
            await _settle(pilot, tui)
            assert screen.metric == "overlay"
            assert "Overlay" in _text(screen.query_one("#chart-hint", Static))

        self._open(http, 1, body, tab="tab-charts")


async def _edit_preferences_and_global_config(pilot: Pilot[None], tui: GreenhouseApp, http: TestClient) -> None:
    """Edit the preferences (``p``) and the global config (``g``) from the settings screen."""
    await pilot.press("p")
    await pilot.pause()
    await _submit_form(pilot, tui, timezone="Europe/Rome", dry_run_global=True)
    prefs = http.get("/api/v1/preferences").json()
    assert prefs["timezone"] == "Europe/Rome"
    assert prefs["dry_run_global"] is True

    await pilot.press("g")
    await pilot.pause()
    await _submit_form(pilot, tui, interval_hours="18")
    assert http.get("/api/v1/config/global").json()["interval_hours"] == EDITED_GLOBAL_INTERVAL_HOURS


async def _vacation_add_edit_delete(pilot: Pilot[None], tui: GreenhouseApp, http: TestClient) -> None:
    """Add, edit and delete a vacation window from the settings screen."""
    await pilot.press("n")
    await pilot.pause()
    await _submit_form(pilot, tui, starts_at="2030-07-01 08:00", ends_at="2030-07-15 20:00", notes="sea")
    items = http.get("/api/v1/vacation").json()["items"]
    assert [v["notes"] for v in items] == ["sea"]
    table = tui.screen.query_one("#vacation-table", DataTable)
    assert table.row_count == 1

    await pilot.press("u")
    await pilot.pause()
    await _submit_form(pilot, tui, contact_email="neighbour@example.com")
    assert http.get("/api/v1/vacation").json()["items"][0]["contact_email"] == "neighbour@example.com"

    await pilot.press("delete")
    await pilot.pause()
    await _confirm(pilot, tui)
    assert http.get("/api/v1/vacation").json()["items"] == []


class TestSettingsScreen:
    """Settings screen: preferences, global config, vacation and account."""

    def test_preferences_global_and_vacation(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Preferences, global config and vacation windows are editable from the settings screen."""
        http, _ = greenhouse

        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("o")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, SettingsScreen)
                assert "test-admin" in _text(tui.screen.query_one("#account", Static))
                await _edit_preferences_and_global_config(pilot, tui, http)
                await _vacation_add_edit_delete(pilot, tui, http)

        _run(scenario())

    def test_logout_clears_token(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Logging out removes the stored token file."""
        http, _ = greenhouse
        token_path = Path(os.environ["XDG_CONFIG_HOME"]) / "greenhouse" / "token"
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text("stale-token")

        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("o")
                await _settle(pilot, tui)
                await pilot.press("O")
                await _settle(pilot, tui)

        _run(scenario())
        assert not token_path.exists()


async def _pause_then_resume_scheduler(pilot: Pilot[None], tui: GreenhouseApp, http: TestClient) -> None:
    """Pause the scheduler from the system screen, then resume it; both ask for confirmation."""
    screen = tui.screen
    assert isinstance(screen, SystemScreen), screen
    await pilot.press("p")
    await pilot.pause()
    await _confirm(pilot, tui)
    assert http.get("/api/v1/preferences").json()["scheduler_paused"] is True
    assert screen.paused is True
    await pilot.press("p")  # resume asks too: automatic irrigation restarts
    await pilot.pause()
    await _confirm(pilot, tui)
    assert http.get("/api/v1/preferences").json()["scheduler_paused"] is False


# The scheduler's job list as the mock server serves it: a built-in job listed twice, and one ad-hoc job.
_SCHEDULER_JOBS = [
    {
        "id": "sensor_sync",
        "name": "Sensor data sync",
        "trigger": "interval",
        "next_run_time": None,
        "core": True,
    },
    {
        "id": "sensor_sync",
        "name": "Sensor data sync",
        "trigger": "interval",
        "next_run_time": None,
        "core": True,
    },
    {"id": "pump_watch_3", "name": "Pump watcher", "trigger": "date", "next_run_time": None, "core": False},
]


async def _delete_builtin_job_is_refused(
    pilot: Pilot[None], tui: GreenhouseApp, table: DataTable[object], seen: list[tuple[str, str]]
) -> None:
    """``delete`` on a built-in job is refused client-side: no dialog, no request, an explanatory toast."""
    table.move_cursor(row=table.get_row_index("sensor_sync"))
    await pilot.press("delete")
    await pilot.pause()
    assert not isinstance(tui.screen, ConfirmScreen)
    assert not [r for r in seen if r[0] == "DELETE"]
    assert any("built-in job" in n.message for n in tui._notifications)  # noqa: SLF001 — Textual keeps shown toasts only in App._notifications


class TestSearchAndSystem:
    """Global search and the system screen's maintenance actions."""

    def test_search_opens_cluster(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Picking a search result opens the matching cluster screen."""
        http, _ = greenhouse

        async def scenario() -> None:
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await pilot.press("slash")
                await pilot.pause()
                assert isinstance(tui.screen, SearchScreen)
                await pilot.press(*"citrus")
                await pilot.pause(0.4)
                await _settle(pilot, tui)
                table = tui.screen.query_one(DataTable)
                assert table.row_count >= 1
                table.focus()
                await pilot.press("enter")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, ClusterScreen)
                assert tui.screen.cluster_id == BALCONY_CLUSTER_ID

        _run(scenario())

    def test_search_columns_fit_the_latest_results(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Column widths follow each result set: a long early hit no longer keeps the table wide."""
        http, _ = greenhouse
        hits: dict[str, list[dict[str, object]]] = {
            "lo": [{"entity_type": "plant", "label": "L" * 60, "sublabel": "D" * 40, "href": "/clusters/1"}],
            "lon": [{"entity_type": "plant", "label": "Fern", "sublabel": "", "href": "/clusters/1"}],
        }

        async def scenario() -> None:
            tui = GreenhouseApp(
                "http://testserver", client_factory=_canned_search_factory(http, hits), refresh_seconds=0
            )
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await pilot.press("slash")
                await pilot.pause()
                table = tui.screen.query_one(DataTable)
                await pilot.press(*"lo")
                await pilot.pause(0.4)
                await _settle(pilot, tui)
                wide = sum(c.get_render_width(table) for c in table.columns.values())
                await pilot.press("n")
                await pilot.pause(0.4)
                await _settle(pilot, tui)
                assert table.get_row_at(0)[1] == "Fern"
                narrow = sum(c.get_render_width(table) for c in table.columns.values())
                assert narrow < wide - 50

        _run(scenario())

    def test_system_maintenance_actions(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """The system screen's maintenance keys send their requests and refresh the panels."""
        http, _ = greenhouse

        async def scenario() -> None:
            tui = _app(http, log)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("s")
                await _settle(pilot, tui)
                screen = tui.screen
                assert "Data quality" in _text(screen.query_one("#quality-hint", Static))
                await _pause_then_resume_scheduler(pilot, tui, http)

                log.clear()
                await pilot.press("H")
                await _settle(pilot, tui)
                await pilot.press("P")  # plant-DB sync asks first; sensor sync (S) runs on the key press
                await pilot.pause()
                await _confirm(pilot, tui)
                await pilot.press("S")
                await _settle(pilot, tui)
                assert writes(log) == [
                    ("POST", "/api/v1/plants/health/snapshot", None),
                    ("POST", "/api/v1/plants/sync", {"plant_id": None, "cluster_id": None}),
                    ("POST", "/api/v1/sync", {"hours": 24}),
                ]

        log: list[RequestLog] = []
        _run(scenario())

    def test_remove_scheduler_job(self) -> None:
        """Mock server: the real APScheduler is a process-wide singleton shared by all tests."""
        jobs = _SCHEDULER_JOBS
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            if request.method == "DELETE":
                return httpx.Response(200, json={"success": True})
            if request.url.path == "/api/v1/scheduler/jobs":
                return httpx.Response(200, json=jobs)
            return httpx.Response(200, json={})

        def factory(_token: str | None) -> IrrigationClient:
            return IrrigationClient(base_url="http://mock", token="", transport=httpx.MockTransport(handler))

        async def scenario() -> None:
            tui = GreenhouseApp("http://mock", client_factory=factory, refresh_seconds=0)
            async with tui.run_test(size=SIZE, notifications=True) as pilot:
                await pilot.press("s")
                await _settle(pilot, tui)
                table = tui.screen.query_one("#jobs-table", DataTable)
                assert table.row_count == SCHEDULER_JOB_ROWS  # duplicate pending job collapsed
                assert "built-in" in str(table.get_row("sensor_sync")[0])

                # Built-in job: refused client-side, no dialog, no request.
                await _delete_builtin_job_is_refused(pilot, tui, table, seen)

                # Ad-hoc job: confirm → DELETE.
                table.move_cursor(row=table.get_row_index("pump_watch_3"))
                await pilot.press("delete")
                await pilot.pause()
                await _confirm(pilot, tui)
                assert ("DELETE", "/api/v1/scheduler/jobs/pump_watch_3") in seen

        _run(scenario())


# ── Logic details: exact requests, cancel paths, errors, refresh, pagination ─


class TestRefill:
    """``refill`` keeps the table cursor on the same record across reloads."""

    def _table_app(self, body: Callable[[Pilot[None], TuiDataTable[object]], Awaitable[None]]) -> None:
        class TableApp(App[None]):
            def compose(self) -> ComposeResult:
                yield DataTable(id="t")

        async def scenario() -> None:
            app = TableApp()
            async with app.run_test() as pilot:
                table = app.query_one(DataTable)
                table.add_columns("v")
                # ``refill`` / ``selected_key`` are typed for the TUI's DataTable subclass, but they work on any
                # Textual DataTable — and this test drives a plain one on purpose.
                await body(pilot, cast("TuiDataTable[object]", table))

        _run(scenario())

    def test_cursor_follows_record_across_reload(self) -> None:
        """A reload that shifts rows keeps the cursor on the selected record, not the row index."""

        async def body(_pilot: Pilot[None], table: TuiDataTable[object]) -> None:
            refill(table, [("1", ["a"]), ("2", ["b"]), ("3", ["c"])])
            table.move_cursor(row=2)
            assert selected_key(table) == "3"
            # Reload with a new row on top: the cursor stays on record 3, not row index 2.
            refill(table, [("0", ["new"]), ("1", ["a"]), ("2", ["b"]), ("3", ["c"])])
            assert selected_key(table) == "3"

        self._table_app(body)

    def test_cursor_clamps_when_record_disappears(self) -> None:
        """When the selected record disappears the cursor clamps to a remaining row, or none."""

        async def body(_pilot: Pilot[None], table: TuiDataTable[object]) -> None:
            refill(table, [("1", ["a"]), ("2", ["b"]), ("3", ["c"])])
            table.move_cursor(row=2)
            refill(table, [("1", ["a"]), ("2", ["b"])])
            assert selected_key(table) == "2"
            refill(table, [])
            assert selected_key(table) is None

        self._table_app(body)


class TestTableColumnWidths:
    """A table painted before its idle-time measurement must not keep serving the label-only widths."""

    TITLE = "Low battery: Monstera probe"

    def test_paint_before_measurement_repaints_at_the_measured_width(self) -> None:
        """A paint that races the column measurement is redone at the measured width (stale-render fix)."""

        class TableApp(App[None]):
            # A fixed height, like ``#alerts-table``: an auto-height table resizes when the row lands, and
            # Textual's resize handler would invalidate the stale render by itself.
            CSS = "#t { height: 1fr; }"

            def compose(self) -> ComposeResult:
                yield TuiDataTable(id="t")

        async def scenario() -> None:
            app = TableApp()
            async with app.run_test(size=(80, 10)) as pilot:
                table = app.query_one(TuiDataTable)
                table.add_columns("ID", "Title", "Cluster")
                await pilot.pause()
                refill(table, [("1", ["1", self.TITLE, "2"])])
                assert table._new_rows, "the row must still be unmeasured for this paint to race the measurement"  # noqa: SLF001 — DataTable's pending-measurement state is private
                # What the screen's update timer does when it fires before the table's idle (a busy CPU).
                table.render_lines(Region(0, 0, table.size.width, table.size.height))
                await _settle_until(pilot, app, lambda: not table._require_update_dimensions)  # noqa: SLF001 — DataTable's pending-measurement state is private
                header, row = (table.render_line(y).text for y in range(2))
                assert self.TITLE in row, row
                assert header.index("Cluster") == row.index(self.TITLE) + len(self.TITLE) + 2, (header, row)

        _run(scenario())

    def test_every_tui_table_is_the_width_safe_one(self) -> None:
        """Textual's own ``DataTable`` would bring the stale-width paint back; screens use ``tui.table``'s."""
        offenders: list[str] = []
        for path in sorted(Path(tui_pkg.__file__).parent.rglob("*.py")):
            if path.name == "table.py":
                continue
            offenders.extend(
                path.name
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith("textual")
                and any(alias.name == "DataTable" for alias in node.names)
            )
        assert offenders == []


class TestSpriteView:
    """SpriteView widget integration with Textual's animation machinery."""

    @pytest.mark.parametrize("animate", [True, False])
    def test_widget_animate_still_works_on_a_sprite(self, *, animate: bool) -> None:
        """``Widget.animate`` caches its animator in ``_animate``; the sprite's animation flag must not live there."""

        class SpriteApp(App[None]):
            def compose(self) -> ComposeResult:
                yield SpriteView(lambda frame: f"frame {frame}", animate=animate, id="sprite")

        async def scenario() -> None:
            app = SpriteApp()
            async with app.run_test() as pilot:
                sprite = app.query_one("#sprite", SpriteView)
                levelled = cast("_Levelled", sprite)  # the test gives the sprite an ad-hoc ``level`` to animate
                levelled.level = 0.0
                sprite.animate("level", 1.0, duration=0.01)
                await pilot.wait_for_animation()
                assert levelled.level == 1.0

        _run(scenario())


class TestExactRequests:
    """Exact HTTP requests the TUI sends for each action (method, path and body)."""

    def _open(
        self, http: TestClient, log: list[RequestLog], cluster_id: int, body: ClusterBody, tab: str | None = None
    ) -> None:
        async def scenario() -> None:
            tui = _app(http, log)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await tui.push_screen(ClusterScreen(cluster_id))
                await _settle(pilot, tui)
                if tab:
                    tui.screen.query_one("TabbedContent", TabbedContent).active = tab
                    await _settle(pilot, tui)
                log.clear()
                screen = tui.screen
                assert isinstance(screen, ClusterScreen), screen
                await body(pilot, tui, screen)

        _run(scenario())

    def test_irrigate_options_map_to_request_body(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """The irrigate dialog's checkboxes map one-to-one onto the request body."""
        http, _ = greenhouse
        log: list[RequestLog] = []

        async def body(pilot: Pilot[None], tui: GreenhouseApp, _screen: ClusterScreen) -> None:
            await pilot.press("i")
            await pilot.pause()
            tui.screen.query_one("#dry-run", Checkbox).value = False
            tui.screen.query_one("#force", Checkbox).value = True
            await pilot.click("#run")
            await _settle(pilot, tui)
            assert writes(log)[0] == (
                "POST",
                "/api/v1/clusters/1/irrigate",
                {"temp_override": None, "dry_run": False, "no_sync": False, "force": True},
            )

        self._open(http, log, 1, body)

    def test_water_now_blank_minutes_uses_device_default(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """A blank minutes field omits the duration so the device default applies."""
        http, wiring = greenhouse
        log: list[RequestLog] = []

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            assert screen.summary is not None
            await pilot.press("w")
            await pilot.pause()
            await pilot.click("#start")
            await _settle(pilot, tui)
            assert writes(log)[0] == (
                "POST",
                f"/api/v1/irrigators/{screen.summary.irrigator_id}/start",
                {"minutes": None},
            )
            assert ("start", screen.summary.irrigator_id, None) in wiring.irrigator.calls

        self._open(http, log, 1, body)

    @pytest.mark.parametrize(
        ("key", "tab"),
        [("i", None), ("w", None), ("x", None), ("c", None), ("n", "tab-plants"), ("u", "tab-config"), ("D", None)],
    )
    def test_cancel_never_writes(
        self, greenhouse: tuple[TestClient, FakeDeviceWiring], key: str, tab: str | None
    ) -> None:
        """Cancelling any dialog sends no write request."""
        http, wiring = greenhouse
        log: list[RequestLog] = []

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            await pilot.press(key)
            await pilot.pause()
            assert tui.screen is not screen  # a dialog opened
            await pilot.press("escape")
            await _settle(pilot, tui)
            assert tui.screen is screen
            assert writes(log) == []
            assert not wiring.irrigator.calls

        self._open(http, log, 1, body, tab)

    def test_check_cluster_after_confirm(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Check runs only after confirmation and posts exactly one check request."""
        http, _ = greenhouse
        log: list[RequestLog] = []

        async def body(pilot: Pilot[None], tui: GreenhouseApp, _screen: ClusterScreen) -> None:
            await pilot.press("c")
            await pilot.pause()
            await _confirm(pilot, tui)
            assert writes(log) == [("POST", "/api/v1/clusters/1/check", None)]

        self._open(http, log, 1, body)

    def test_new_and_edit_route_to_the_active_tab(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """New / edit / delete keys do nothing on tabs that have no editable resource."""
        http, _ = greenhouse
        log: list[RequestLog] = []

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            for tab in ("tab-decisions", "tab-history", "tab-charts", "tab-insights"):
                screen.query_one("TabbedContent", TabbedContent).active = tab
                await _settle(pilot, tui)
                for key in ("n", "u", "delete"):
                    await pilot.press(key)
                    await pilot.pause()
                    assert tui.screen is screen, (tab, key)
            assert writes(log) == []

        self._open(http, log, 1, body)

    def test_existing_irrigator_cannot_be_added_twice(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """A cluster that already has a pump does not offer to add a second one."""
        http, _ = greenhouse
        log: list[RequestLog] = []

        async def body(pilot: Pilot[None], tui: GreenhouseApp, screen: ClusterScreen) -> None:
            await pilot.press("n")  # Overview tab, cluster already has a pump
            await pilot.pause()
            assert tui.screen is screen
            assert writes(log) == []

        self._open(http, log, 1, body)

    def test_alert_resolve_and_rescan(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Resolve and re-scan send their alert requests from the alerts table."""
        http, _ = greenhouse
        log: list[RequestLog] = []

        async def scenario() -> None:
            tui = _app(http, log)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("a")
                await _settle(pilot, tui)
                table = tui.screen.query_one(DataTable)
                table.move_cursor(row=table.get_row_index("1"))
                log.clear()
                await pilot.press("v")
                await _settle(pilot, tui)
                await pilot.press("y")
                await _settle(pilot, tui)
                assert writes(log) == [
                    ("POST", "/api/v1/alerts/1/resolve", None),
                    ("POST", "/api/v1/alerts/sync", None),
                ]
                assert http.get("/api/v1/alerts/1").json()["status"] == "resolved"

        _run(scenario())

    def test_dashboard_check_all_and_sync(self, greenhouse: tuple[TestClient, FakeDeviceWiring]) -> None:
        """Dashboard check-all (after confirmation) and sensor sync send their requests."""
        http, _ = greenhouse
        log: list[RequestLog] = []

        async def scenario() -> None:
            tui = _app(http, log)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                log.clear()
                await pilot.press("c")
                await pilot.pause()
                await _confirm(pilot, tui)
                await pilot.press("S")
                await _settle(pilot, tui)
                assert writes(log) == [("POST", "/api/v1/check", None), ("POST", "/api/v1/sync", {"hours": 24})]

        _run(scenario())


def _mock_app(handler: Callable[[httpx.Request], httpx.Response], refresh_seconds: float = 0) -> GreenhouseApp:
    def factory(_token: str | None) -> IrrigationClient:
        return IrrigationClient(base_url="http://mock", token="", transport=httpx.MockTransport(handler))

    return GreenhouseApp("http://mock", client_factory=factory, refresh_seconds=refresh_seconds)


def _paged_activity_handler(requests: list[dict[str, str]]) -> Callable[[httpx.Request], httpx.Response]:
    """Mock activity endpoint: a full first page with a cursor, then one older row; records each query."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/v1/activity":
            return httpx.Response(200, json={})
        params = dict(request.url.params)
        requests.append(params)
        item = {"timestamp": 1000, "source": "s", "entity_type": "cluster", "entity_id": 1, "severity": "info"}
        if "before" not in params:
            items = [{**item, "id": i, "code": f"c{i}", "message": "m"} for i in range(ACTIVITY_PAGE_SIZE)]
            return httpx.Response(200, json={"items": items, "next_cursor": 999})
        return httpx.Response(200, json={"items": [{**item, "id": 500, "code": "old", "message": "m"}]})

    return handler


def _canned_search_factory(http: TestClient, hits: dict[str, list[dict[str, object]]]) -> ClientFactory:
    """Client factory whose ``search`` answers from ``hits`` (query → hit list)."""
    base = tui_client_factory(http)

    def fake_search(query: str, limit: int = 20) -> JSONObject:
        del limit  # the screen passes ``limit=``; the canned hits ignore it
        return {"hits": hits.get(query, [])}

    def factory(token: str | None) -> IrrigationClient:
        client = base(token)
        client.search = fake_search  # type: ignore[method-assign]  # canned hits for this client only
        return client

    return factory


class TestResilience:
    """Error handling, empty installs, auto-refresh and pagination."""

    def test_server_error_is_toasted_not_raised(self) -> None:
        """A 5xx from the server becomes a notification instead of an exception."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/clusters":
                return httpx.Response(500, json={"detail": "database is locked"})
            return httpx.Response(200, json={})

        async def scenario() -> None:
            tui = _mock_app(handler)
            async with tui.run_test(size=SIZE, notifications=True) as pilot:
                await _settle(pilot, tui)
                messages = [n.message for n in tui._notifications]  # noqa: SLF001 — Textual keeps shown toasts only in App._notifications
                assert "database is locked" in messages
                assert "Cannot reach the server" in _text(tui.screen.query_one("#dashboard-empty", Static))

        _run(scenario())

    def test_empty_install_prompts_to_create_cluster(self) -> None:
        """With no clusters the dashboard invites the user to create one."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[] if request.url.path == "/api/v1/clusters" else {})

        async def scenario() -> None:
            tui = _mock_app(handler)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                assert "press n to create one" in _text(tui.screen.query_one("#dashboard-empty", Static))

        _run(scenario())

    def test_auto_refresh_polls_again(self) -> None:
        """With a refresh period set, the dashboard polls the server again on its own."""
        hits: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/clusters":
                hits.append(1)
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={})

        async def scenario() -> None:
            tui = _mock_app(handler, refresh_seconds=0.2)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.pause(1.2)  # never idle by design, so no _settle here
                assert len(hits) >= MIN_AUTO_REFRESH_POLLS

        _run(scenario())

    def test_overlapping_dashboard_reloads_do_not_crash(self) -> None:
        """S4: reloads that outlast each other (slow status calls) neither crash nor duplicate cards."""

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/api/v1/clusters":
                return httpx.Response(200, json=[{"id": 1, "name": "One"}, {"id": 2, "name": "Two"}])
            if path.endswith("/status"):
                time.sleep(0.02)
                cid = int(path.split("/")[-2])
                return httpx.Response(200, json={"cluster": {"id": cid, "name": f"C{cid}"}, "sensors": []})
            return httpx.Response(200, json={})

        async def scenario() -> None:
            tui = _mock_app(handler)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                screen = tui.screen
                assert isinstance(screen, DashboardScreen), screen
                for _ in range(6):
                    screen.run_worker(screen.load(), group="overlap")
                    await pilot.pause(0.01)
                await _settle(pilot, tui)
                cards = [str(w.id) for w in screen.query(ClusterCard)]
                assert sorted(cards) == ["cluster-card-1", "cluster-card-2"]
                screen.reload()
                await pilot.pause(0.03)  # let the reloads start; the app then tears down mid-load

        _run(scenario())

    def test_app_closes_its_http_clients_on_exit(self) -> None:
        """The TUI's shared client — and the one replaced by a sign-in — are closed when the app exits."""
        made: list[IrrigationClient] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/auth/login":
                return httpx.Response(200, json={"access_token": "", "username": "admin"})
            return httpx.Response(200, json=[] if request.url.path == "/api/v1/clusters" else {})

        def factory(_token: str | None) -> IrrigationClient:
            client = IrrigationClient(base_url="http://mock", token="", transport=httpx.MockTransport(handler))
            made.append(client)
            return client

        async def scenario() -> None:
            tui = GreenhouseApp("http://mock", client_factory=factory, refresh_seconds=0)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await tui._login("admin", "pw")  # noqa: SLF001 — drives the sign-in worker without the dialog
                await _settle(pilot, tui)
                assert not any(c.http.is_closed for c in (made[0], tui.client))

        _run(scenario())
        assert len(made) == CLIENTS_MADE  # session client, anonymous login client, signed-in client
        assert all(c.http.is_closed for c in made)

    def test_r_reloads_active_screen(self) -> None:
        """The r key reloads the active screen on demand."""
        hits: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/clusters":
                hits.append(1)
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={})

        async def scenario() -> None:
            tui = _mock_app(handler)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await pilot.press("r")
                await _settle(pilot, tui)
                assert len(hits) == POLLS_AFTER_RELOAD

        _run(scenario())

    def test_activity_pagination_uses_cursor(self) -> None:
        """Loading more activity passes the previous page's cursor as ``before``."""
        requests: list[dict[str, str]] = []

        async def scenario() -> None:
            tui = _mock_app(_paged_activity_handler(requests))
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("l")
                await _settle(pilot, tui)
                table = tui.screen.query_one(DataTable)
                assert table.row_count == ACTIVITY_PAGE_SIZE
                await pilot.press("n")
                await _settle(pilot, tui)
                assert table.row_count == ACTIVITY_PAGE_SIZE + 1
                assert requests[-1]["before"] == "999"
                await pilot.press("n")  # no cursor left → no request
                await _settle(pilot, tui)
                assert len([r for r in requests if "before" in r]) == 1

        _run(scenario())

    def test_login_failure_reprompts(self) -> None:
        """A failed login keeps the login dialog open for another attempt."""
        application, engine = _make_stubbed_app(bypass_auth=False)

        def factory(token: str | None) -> IrrigationClient:
            client = IrrigationClient(base_url="http://testserver", token=token or "")
            client.http = TestClient(application, raise_server_exceptions=False)
            return client

        async def scenario() -> None:
            tui = GreenhouseApp("http://testserver", client_factory=factory, refresh_seconds=0)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                tui.screen.query_one("#username", Input).value = "nobody"
                tui.screen.query_one("#password", Input).value = "wrong"
                await pilot.click("#login")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, LoginScreen)
                assert "Login failed" in str(tui.screen.query_one(".dialog-error").render())

        try:
            _run(scenario())
            assert not (Path(os.environ["XDG_CONFIG_HOME"]) / "greenhouse" / "token").exists()
        finally:
            engine.dispose()


def test_failed_shielded_card_rebuild_is_logged_with_traceback() -> None:
    """A rebuild whose worker was cancelled is awaited by no one; its failure is logged, not left unretrieved."""
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign,assignment]  # collect records on this handler only
    dashboard.logger.addHandler(handler)

    async def boom() -> None:
        msg = "mount failed"
        raise RuntimeError(msg)

    async def scenario() -> None:
        task = asyncio.ensure_future(boom())
        task.add_done_callback(_log_rebuild_failure)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    try:
        _run(scenario())
    finally:
        dashboard.logger.removeHandler(handler)
    assert [r.getMessage() for r in records] == ["dashboard card rebuild failed"]
    assert records[0].exc_info is not None
