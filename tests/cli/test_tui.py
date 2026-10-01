"""Tests for the Textual TUI (``greenhouse tui``).

Pure helpers (sprites, formatting, summaries) are unit-tested directly. The
screens are driven with Textual's headless ``Pilot`` against the *real*
FastAPI app (in-memory SQLite + fake devices, seeded by ``tui_fixtures``), so
every client call the TUI makes is checked against the live route and schema.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from rich.console import Console
from textual.widgets import Checkbox, DataTable, Input, Static
from textual.worker import WorkerCancelled
from typer.testing import CliRunner

from cli.tui_fixtures import seed_greenhouse, tui_client_factory, writes
from greenhouse_cli.client import IrrigationClient
from greenhouse_cli.main import app as cli_app
from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui.app import GreenhouseApp
from greenhouse_cli.tui.model import is_watering, sparkline_points, summarize
from greenhouse_cli.tui.screens.activity import ActivityScreen
from greenhouse_cli.tui.screens.alerts import AlertsScreen
from greenhouse_cli.tui.screens.cluster import ClusterScreen
from greenhouse_cli.tui.screens.dashboard import DashboardScreen
from greenhouse_cli.tui.screens.forms import Field, FormScreen, parse_value
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
from greenhouse_cli.tui.widgets import ClusterCard, MetricChart, refill, selected_key
from server.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME, _make_stubbed_app

SIZE = (160, 50)


@pytest.fixture(autouse=True)
def _isolated_token_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("GREENHOUSE_API_TOKEN", raising=False)


@pytest.fixture
def greenhouse():
    """(TestClient, fake-device wiring) for a seeded, auth-bypassed app."""
    application, engine = _make_stubbed_app(bypass_auth=True)
    http = TestClient(application, raise_server_exceptions=False)
    seed_greenhouse(http, engine)
    yield http, application.state.fake_devices
    engine.dispose()


def _app(http: TestClient, log: list | None = None, refresh_seconds: float = 0) -> GreenhouseApp:
    return GreenhouseApp(
        "http://testserver", client_factory=tui_client_factory(http, log), refresh_seconds=refresh_seconds
    )


async def _settle(pilot, tui: GreenhouseApp) -> None:
    """Let workers finish and the DOM catch up.

    Exclusive workers (reloads, chart loads) legitimately cancel the run they
    supersede, so a cancelled worker is not a failure here.
    """
    for _ in range(3):
        await pilot.pause()
        try:
            await tui.workers.wait_for_complete()
        except WorkerCancelled:
            pass
    await pilot.pause()


def _run(coro) -> None:
    asyncio.run(coro)


def _text(widget: Static) -> str:
    """Plain text of whatever a Static widget is currently showing."""
    return _render(widget.content, width=200)


def _render(renderable, width: int = 40) -> str:
    console = Console(width=width, color_system=None, record=True)
    console.print(renderable)
    return console.export_text()


# ── Sprites ─────────────────────────────────────────────────────────────────


class TestSprites:
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
    def test_mood_with_fallback_band(self, moisture, expected):
        assert mood_for(moisture) is expected

    def test_mood_uses_supplied_band(self):
        assert mood_for(50.0, band_min=55.0, band_max=80.0) is Mood.THIRSTY
        assert mood_for(50.0, band_min=20.0, band_max=40.0) is Mood.SOAKED

    def test_half_block_rendering(self):
        sprite = PixelSprite(("AB.", "A.B"), {"A": "#ff0000", "B": "#00ff00"})
        assert sprite.width == 3 and sprite.height == 1
        # A over A → full ▀ ; B over transparent → ▀ ; transparent over B → ▄
        assert _render(sprite).splitlines()[0] == "▀▀▄"

    def test_odd_row_count_is_padded(self):
        sprite = PixelSprite(("A", "A", "A"), {"A": "#ffffff"})
        assert sprite.height == 2
        assert _render(sprite).splitlines()[:2] == ["▀", "▀"]

    @pytest.mark.parametrize("category", ["tropical", "fern", "succulent", "cacti", "fruit_tree", None, "mystery"])
    def test_every_category_draws_a_potted_plant(self, category):
        sprite = plant_sprite(category, Mood.OK)
        assert sprite.width == 16
        assert sprite.height == 8
        assert sprite.rows[-1].strip(".")  # pot base is painted

    def test_plants_animate(self):
        frames = {plant_sprite("tropical", Mood.OK, f).rows for f in range(4)}
        assert len(frames) > 1

    def test_thriving_sparkles_and_wilting_drops_leaves(self):
        assert any("K" in r for r in plant_sprite("fern", Mood.THRIVING, 0).rows)
        wilting = [plant_sprite("fern", Mood.WILTING, f).rows for f in range(3)]
        assert len(set(wilting)) == 3  # the falling leaf moves every frame

    def test_watering_overlays_drops(self):
        assert any("W" in r for r in plant_sprite("cacti", Mood.THIRSTY, 1, watering=True).rows)
        assert not any("W" in r for r in plant_sprite("cacti", Mood.THIRSTY, 1).rows)

    def test_mood_recolours_foliage(self):
        assert plant_sprite("fern", Mood.THRIVING).palette["G"] != plant_sprite("fern", Mood.WILTING).palette["G"]

    def test_category_aliases(self):
        assert normalize_category("Cactus") == "cacti"
        assert normalize_category("fruit-tree") == "fruit_tree"
        assert normalize_category(None) == "generic"

    def test_misc_sprites(self):
        assert logo_sprite().height > 0
        assert watering_can_sprite(pouring=True, frame=1).rows != watering_can_sprite().rows


# ── Formatting / model ──────────────────────────────────────────────────────


class TestFormatting:
    def test_ago(self):
        assert fmt.ago(None) == "never"
        assert fmt.ago(1000, reference=1030) == "30s ago"
        assert fmt.ago(1000, reference=1000 + 7260) == "2h01m ago"
        assert fmt.ago(1000 + 3 * 86400, reference=1000) == "in 3d0h"

    def test_age(self):
        assert fmt.age(None) == "—"
        assert fmt.age(0) == "0s ago"
        assert fmt.age(300) == "5m ago"

    def test_num(self):
        assert fmt.num(None, "%") == "—"
        assert fmt.num(42.123, "%") == "42.1%"
        assert fmt.num(42.6, "%", 0) == "43%"

    def test_bar(self):
        assert len(fmt.bar(None, 10)) == 10
        gauge = fmt.bar(50, 10, 40, 70)
        assert gauge.plain.startswith("█████")
        assert len(gauge) == 10

    def test_weekday_mask(self):
        assert fmt.weekday_mask(127) == "every day"
        assert fmt.weekday_mask(1 | 4) == "M·W····"


class TestModel:
    STATUS = {
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

    def test_summarize_picks_driest_plant(self):
        s = summarize(self.STATUS, {"threshold": {"min": 40, "max": 70}, "datasets": []})
        assert s.min_moisture == 30.0
        assert s.driest.species == "B"
        assert s.temperature == 22.0
        assert s.newest_reading_at == 200
        assert s.mood is Mood.THIRSTY
        assert [p.mood for p in s.plants] == [Mood.OK, Mood.THIRSTY]
        assert s.irrigator_id == 3 and not s.watering

    def test_sparkline_uses_per_timestamp_minimum(self):
        chart = {"datasets": [{"points": [[1, 50], [2, 40]]}, {"points": [[1, 45], [3, 60]]}]}
        assert sparkline_points(chart) == [45, 40, 60]
        assert sparkline_points(None) == []

    def test_is_watering(self):
        event = {"action": "start", "timestamp": 1000, "duration_minutes": 5}
        assert is_watering(event, reference=1200)
        assert not is_watering(event, reference=1000 + 301)
        assert not is_watering({**event, "action": "stop"}, reference=1001)
        assert not is_watering(None)


# ── Screens (full stack) ────────────────────────────────────────────────────


class TestDashboard:
    def test_cards_for_every_cluster(self, greenhouse):
        http, _ = greenhouse

        async def scenario():
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

    def test_stop_all_requires_confirmation(self, greenhouse):
        http, wiring = greenhouse

        async def scenario():
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
                assert len([c for c in wiring.irrigator.calls if c[0] == "stop"]) == 2

        _run(scenario())

    def test_unreachable_server(self):
        def refuse(request):
            raise httpx.ConnectError("refused")

        def factory(token):
            return IrrigationClient(base_url="http://down", token="", transport=httpx.MockTransport(refuse))

        async def scenario():
            tui = GreenhouseApp("http://down", client_factory=factory, refresh_seconds=0)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                empty = tui.screen.query_one("#dashboard-empty", Static)
                assert "Cannot reach the server" in _text(empty)

        _run(scenario())


class TestClusterScreen:
    def _open(self, http, cluster_id: int, body):
        async def scenario():
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await tui.push_screen(ClusterScreen(cluster_id))
                await _settle(pilot, tui)
                await body(pilot, tui, tui.screen)

        _run(scenario())

    def test_tabs_are_populated(self, greenhouse):
        http, _ = greenhouse

        async def body(pilot, tui, screen):
            assert screen.summary.name == "Living Room"
            assert len(screen.query("PlantTile")) == 2
            assert screen.query_one("#sensors-table", DataTable).row_count == 2
            assert screen.query_one("#plants-table", DataTable).row_count == 2
            assert screen.query_one("#history-table", DataTable).row_count == 3
            decision = _text(screen.query_one("#decision-panel", Static))
            assert "Decision engine" in decision
            config = _text(screen.query_one("#config-panel"))
            assert "duration_minutes" in config
            assert screen.query_one("#windows-table", DataTable).row_count == 0

        self._open(http, 1, body)

    def test_metric_and_range_cycle(self, greenhouse):
        http, _ = greenhouse

        async def body(pilot, tui, screen):
            screen.query_one("TabbedContent").active = "tab-charts"
            await pilot.pause()
            await pilot.press("m")
            await _settle(pilot, tui)
            assert screen.metric == "temperature"
            await pilot.press("]")
            await _settle(pilot, tui)
            assert screen.hours == 72
            await pilot.press("[", "[", "[")
            await _settle(pilot, tui)
            assert screen.hours == 6
            assert "Temperature" in _text(screen.query_one("#chart-hint", Static))
            assert isinstance(screen.query_one("#metric-chart"), MetricChart)

        self._open(http, 1, body)

    def test_irrigate_dry_run_does_not_actuate(self, greenhouse):
        http, wiring = greenhouse

        async def body(pilot, tui, screen):
            await pilot.press("i")
            await pilot.pause()
            assert isinstance(tui.screen, IrrigateScreen)
            assert tui.screen.query_one("#dry-run", Checkbox).value is True
            await pilot.click("#run")
            await _settle(pilot, tui)
            assert not [c for c in wiring.irrigator.calls if c[0] == "start"]

        self._open(http, 1, body)

    def test_water_now_and_stop(self, greenhouse):
        http, wiring = greenhouse

        async def body(pilot, tui, screen):
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

    def test_sensor_only_cluster_has_no_irrigator_actions(self, greenhouse):
        http, wiring = greenhouse

        async def body(pilot, tui, screen):
            assert screen.summary.irrigator_id is None
            await pilot.press("w")
            await pilot.pause()
            assert isinstance(tui.screen, ClusterScreen)
            assert not wiring.irrigator.calls

        self._open(http, 3, body)


class TestOtherScreens:
    def test_alerts_acknowledge_and_filter(self, greenhouse):
        http, _ = greenhouse

        async def scenario():
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("a")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, AlertsScreen)
                table = tui.screen.query_one(DataTable)
                assert table.row_count == 2
                await pilot.press("k")
                await _settle(pilot, tui)
                assert table.row_count == 1
                assert http.get("/api/v1/alerts", params={"status": "acknowledged"}).json()["items"]
                await pilot.press("f")
                await _settle(pilot, tui)
                assert tui.screen.status_filter == "acknowledged"
                assert table.row_count == 1

        _run(scenario())

    def test_activity_feed(self, greenhouse):
        http, _ = greenhouse

        async def scenario():
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("l")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, ActivityScreen)
                assert tui.screen.query_one(DataTable).row_count == 3
                await pilot.press("f")
                await _settle(pilot, tui)
                assert tui.screen.severity == "warning"
                assert tui.screen.query_one(DataTable).row_count == 1

        _run(scenario())

    def test_system_screen(self, greenhouse):
        http, _ = greenhouse

        async def scenario():
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("s")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, SystemScreen)
                assert tui.screen.query_one("#devices-table", DataTable).row_count == 6
                assert tui.screen.query_one("#jobs-table", DataTable).row_count >= 1
                assert "automatic runs" in _text(tui.screen.query_one("#scheduler-panel"))

        _run(scenario())


class TestLogin:
    def test_401_prompts_login_and_stores_token(self):
        application, engine = _make_stubbed_app(bypass_auth=False)
        http = TestClient(application, raise_server_exceptions=False)

        def factory(token):
            client = IrrigationClient(base_url="http://testserver", token=token or "")
            client.http = TestClient(application, raise_server_exceptions=False)
            if token:
                client.http.headers["Authorization"] = f"Bearer {token}"
            return client

        async def scenario():
            tui = GreenhouseApp("http://testserver", client_factory=factory, refresh_seconds=0)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                assert isinstance(tui.screen, LoginScreen)
                tui.screen.query_one("#username", Input).value = TEST_ADMIN_USERNAME
                tui.screen.query_one("#password", Input).value = TEST_ADMIN_PASSWORD
                await pilot.click("#login")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, DashboardScreen)
                assert "Cannot reach" not in _text(tui.screen.query_one("#dashboard-empty", Static))

        try:
            assert http.get("/api/v1/clusters").status_code == 401
            _run(scenario())
            token_path = Path(os.environ["XDG_CONFIG_HOME"]) / "greenhouse" / "token"
            assert token_path.read_text()
        finally:
            engine.dispose()


# ── CLI wiring ──────────────────────────────────────────────────────────────


class TestTuiCommand:
    def test_launches_with_resolved_server(self, monkeypatch):
        seen = {}
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

    def test_env_server_fallback(self, monkeypatch):
        seen = {}
        monkeypatch.setenv("IRRIGATION_SERVER_URL", "http://192.0.2.10:8000")
        monkeypatch.setattr("greenhouse_cli.tui.run", lambda server, **kw: seen.update(server=server))
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


def test_tui_reaches_every_client_capability():
    import inspect
    import re

    from greenhouse_cli import tui

    src = "\n".join(p.read_text() for p in Path(tui.__file__).parent.rglob("*.py"))
    public = [n for n, _ in inspect.getmembers(IrrigationClient, inspect.isfunction) if not n.startswith("_")]
    missing = [n for n in public if n not in REDUNDANT_READS and not re.search(rf"\.{n}\b", src)]
    assert not missing, f"IrrigationClient methods with no TUI entry point: {missing}"


def _fill(screen, **values) -> None:
    """Set FormScreen inputs by field name."""
    for name, value in values.items():
        screen.query_one(f"#field-{name}").value = value


async def _submit_form(pilot, tui, **values) -> None:
    assert isinstance(tui.screen, FormScreen), tui.screen
    _fill(tui.screen, **values)
    await pilot.click("#submit")
    await _settle(pilot, tui)


async def _confirm(pilot, tui) -> None:
    assert isinstance(tui.screen, ConfirmScreen), tui.screen
    await pilot.click("#confirm")
    await _settle(pilot, tui)


class TestForms:
    def test_parse_values(self):
        assert parse_value(Field("a", "A", "int"), " 5 ") == 5
        assert parse_value(Field("a", "A", "float"), "2.5") == 2.5
        assert parse_value(Field("a", "A"), "  ") is None
        assert parse_value(Field("a", "A", "bool"), True) is True
        assert parse_value(Field("a", "A", "json"), '{"x": 1}') == {"x": 1}
        assert isinstance(parse_value(Field("a", "A", "datetime"), "2026-10-01 08:30"), int)

    @pytest.mark.parametrize(
        ("field", "raw", "message"),
        [
            (Field("a", "Name", required=True), "", "Name is required"),
            (Field("a", "Minutes", "int"), "abc", "whole number"),
            (Field("a", "Starts", "datetime"), "tomorrow", "YYYY-MM-DD HH:MM"),
            (Field("a", "Config", "json"), "[1]", "JSON object"),
        ],
    )
    def test_parse_errors(self, field, raw, message):
        with pytest.raises(ValueError, match=message):
            parse_value(field, raw)

    def test_invalid_form_stays_open(self, greenhouse):
        http, _ = greenhouse

        async def scenario():
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
    def _open(self, http, cluster_id: int, body, tab: str | None = None):
        async def scenario():
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await tui.push_screen(ClusterScreen(cluster_id))
                await _settle(pilot, tui)
                if tab:
                    tui.screen.query_one("TabbedContent").active = tab
                    await _settle(pilot, tui)
                await body(pilot, tui, tui.screen)

        _run(scenario())

    def test_create_cluster_from_dashboard(self, greenhouse):
        http, _ = greenhouse

        async def scenario():
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await pilot.press("n")
                await pilot.pause()
                await _submit_form(pilot, tui, name="Kitchen", location="sill", environment="indoor")
                assert "Kitchen" in [c["name"] for c in http.get("/api/v1/clusters").json()]
                assert len(tui.screen.query(ClusterCard)) == 4

        _run(scenario())

    def test_plant_add_edit_move_delete(self, greenhouse):
        http, _ = greenhouse

        async def body(pilot, tui, screen):
            await pilot.press("n")
            await pilot.pause()
            await _submit_form(pilot, tui, species="Ficus lyrata", category="tropical", water_needs="medium")
            plants = http.get("/api/v1/clusters/1/plants").json()
            ficus = next(p for p in plants if p["species"] == "Ficus lyrata")
            assert ficus["category"] == "tropical"

            table = screen.query_one("#plants-table", DataTable)
            table.move_cursor(row=table.get_row_index(str(ficus["id"])))
            await pilot.press("u")
            await pilot.pause()
            await _submit_form(pilot, tui, notes="by the window")
            assert http.get(f"/api/v1/plants/{ficus['id']}").json()["notes"] == "by the window"

            await pilot.press("M")
            await _settle(pilot, tui)
            await _submit_form(pilot, tui, target_cluster_id=3)
            assert http.get(f"/api/v1/plants/{ficus['id']}").json()["cluster_id"] == 3

            table.move_cursor(row=0)
            first = int(table.coordinate_to_cell_key(table.cursor_coordinate)[0].value)
            await pilot.press("delete")
            await pilot.pause()
            await _confirm(pilot, tui)
            assert http.get(f"/api/v1/plants/{first}").status_code == 404

        self._open(http, 1, body, tab="tab-plants")

    def test_plant_chart_toggle_and_plant_db_sync(self, greenhouse):
        http, _ = greenhouse

        async def body(pilot, tui, screen):
            assert screen.plant_chart == "health"
            await pilot.press("m")
            await _settle(pilot, tui)
            assert screen.plant_chart == "moisture"
            assert screen.metric == "soil_moisture"  # cluster chart untouched
            await pilot.press("P")
            await _settle(pilot, tui)

        self._open(http, 1, body, tab="tab-plants")

    def test_sensor_add_and_delete(self, greenhouse):
        http, _ = greenhouse

        async def body(pilot, tui, screen):
            await pilot.press("n")
            await pilot.pause()
            await _submit_form(
                pilot, tui, tuya_device_id="fake_tuya_device_aabbccdd", name="Spare probe", type="soil_moisture"
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
            assert http.get(f"/api/v1/clusters/1/sensors/{spare['id']}").status_code == 404

        self._open(http, 1, body, tab="tab-sensors")

    def test_windows_crud(self, greenhouse):
        http, _ = greenhouse

        async def body(pilot, tui, screen):
            await pilot.press("n")
            await pilot.pause()
            await _submit_form(pilot, tui, start_hour="6", end_hour="9", label="morning")
            windows = http.get("/api/v1/clusters/1/windows").json()["windows"]
            assert [(w["start_hour"], w["end_hour"], w["weekday_mask"]) for w in windows] == [(6, 9, 127)]
            assert screen.query_one("#windows-table", DataTable).row_count == 1
            await pilot.press("u")
            await pilot.pause()
            await _submit_form(pilot, tui, end_hour="10")
            assert http.get("/api/v1/clusters/1/windows").json()["windows"][0]["end_hour"] == 10
            await pilot.press("delete")
            await pilot.pause()
            await _confirm(pilot, tui)
            assert http.get("/api/v1/clusters/1/windows").json()["windows"] == []

        self._open(http, 1, body, tab="tab-windows")

    def test_config_edit(self, greenhouse):
        http, _ = greenhouse

        async def body(pilot, tui, screen):
            await pilot.press("u")
            await pilot.pause()
            await _submit_form(pilot, tui, duration_minutes="7", quiet_start_hour="22", quiet_end_hour="6")
            config = http.get("/api/v1/clusters/1/config").json()
            assert (config["duration_minutes"], config["quiet_start_hour"], config["quiet_end_hour"]) == (7, 22, 6)
            assert "7" in _text(screen.query_one("#config-panel"))

        self._open(http, 1, body, tab="tab-config")

    def test_irrigator_edit_detach_attach(self, greenhouse):
        http, _ = greenhouse

        async def body(pilot, tui, screen):
            await pilot.press("u")
            await pilot.pause()
            await _submit_form(pilot, tui, reservoir_l="12.5", config='{"version": "3.5"}')
            irrigator = http.get("/api/v1/clusters/1/irrigator").json()
            assert irrigator["reservoir_l"] == 12.5
            assert irrigator["config"] == {"version": "3.5"}

            await pilot.press("delete")
            await pilot.pause()
            await _confirm(pilot, tui)
            assert http.get("/api/v1/clusters/1/irrigator").status_code == 404
            assert screen.summary.irrigator_id is None

            await pilot.press("n")
            await pilot.pause()
            await _submit_form(
                pilot, tui, tuya_device_id="fake_tuya_device_00112233", name="New pump", type="tuya_cloud"
            )
            assert http.get("/api/v1/clusters/1/irrigator").json()["name"] == "New pump"

        self._open(http, 1, body)

    def test_log_manual(self, greenhouse):
        http, wiring = greenhouse

        async def body(pilot, tui, screen):
            before = screen.query_one("#history-table", DataTable).row_count
            await pilot.press("L")
            await pilot.pause()
            await _submit_form(pilot, tui, minutes="4", notes="watering can")
            assert screen.query_one("#history-table", DataTable).row_count == before + 1
            assert not [c for c in wiring.irrigator.calls if c[0] == "start"]  # logging never actuates

        self._open(http, 1, body, tab="tab-history")

    def test_edit_and_delete_cluster(self, greenhouse):
        http, _ = greenhouse

        async def body(pilot, tui, screen):
            await pilot.press("e")
            await pilot.pause()
            await _submit_form(pilot, tui, name="Desk (office)")
            assert http.get("/api/v1/clusters/3").json()["name"] == "Desk (office)"
            await pilot.press("D")
            await pilot.pause()
            await _confirm(pilot, tui)
            assert http.get("/api/v1/clusters/3").status_code == 404
            assert isinstance(tui.screen, DashboardScreen)

        self._open(http, 3, body)

    def test_insights_tab(self, greenhouse):
        http, _ = greenhouse

        async def body(pilot, tui, screen):
            assert "Care insights" in _text(screen.query_one("#insights-panel"))
            assert "Needs water" in _text(screen.query_one("#insights-panel"))
            assert "events" in _text(screen.query_one("#stats-panel"))
            assert "Learning report" in _text(screen.query_one("#learn-panel"))

        self._open(http, 1, body, tab="tab-insights")

    def test_export_stats_csv(self, greenhouse, tmp_path, monkeypatch):
        http, _ = greenhouse
        monkeypatch.chdir(tmp_path)

        async def body(pilot, tui, screen):
            await pilot.press("E")
            await _settle(pilot, tui)
            exported = tmp_path / "greenhouse-living-room-stats-30d.csv"
            assert exported.exists() and exported.read_text()

        self._open(http, 1, body)

    def test_overlay_metric(self, greenhouse):
        http, _ = greenhouse

        async def body(pilot, tui, screen):
            for _ in range(4):
                await pilot.press("m")
            await _settle(pilot, tui)
            assert screen.metric == "overlay"
            assert "Overlay" in _text(screen.query_one("#chart-hint"))

        self._open(http, 1, body, tab="tab-charts")


class TestSettingsScreen:
    def test_preferences_global_and_vacation(self, greenhouse):
        http, _ = greenhouse

        async def scenario():
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("o")
                await _settle(pilot, tui)
                assert isinstance(tui.screen, SettingsScreen)
                assert "test-admin" in _text(tui.screen.query_one("#account"))

                await pilot.press("p")
                await pilot.pause()
                await _submit_form(pilot, tui, timezone="Europe/Rome", dry_run_global=True)
                prefs = http.get("/api/v1/preferences").json()
                assert prefs["timezone"] == "Europe/Rome" and prefs["dry_run_global"] is True

                await pilot.press("g")
                await pilot.pause()
                await _submit_form(pilot, tui, interval_hours="18")
                assert http.get("/api/v1/config/global").json()["interval_hours"] == 18

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

        _run(scenario())

    def test_logout_clears_token(self, greenhouse):
        http, _ = greenhouse
        token_path = Path(os.environ["XDG_CONFIG_HOME"]) / "greenhouse" / "token"
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text("stale-token")

        async def scenario():
            tui = _app(http)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("o")
                await _settle(pilot, tui)
                await pilot.press("O")
                await _settle(pilot, tui)

        _run(scenario())
        assert not token_path.exists()


class TestSearchAndSystem:
    def test_search_opens_cluster(self, greenhouse):
        http, _ = greenhouse

        async def scenario():
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
                assert tui.screen.cluster_id == 2

        _run(scenario())

    def test_system_maintenance_actions(self, greenhouse):
        http, _ = greenhouse

        async def scenario():
            tui = _app(http, log)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("s")
                await _settle(pilot, tui)
                screen = tui.screen
                assert "Data quality" in _text(screen.query_one("#quality-hint"))

                await pilot.press("p")
                await pilot.pause()
                await _confirm(pilot, tui)
                assert http.get("/api/v1/preferences").json()["scheduler_paused"] is True
                assert screen.paused is True
                await pilot.press("p")  # resume needs no confirmation
                await _settle(pilot, tui)
                assert http.get("/api/v1/preferences").json()["scheduler_paused"] is False

                log.clear()
                await pilot.press("H")
                await _settle(pilot, tui)
                await pilot.press("P")
                await _settle(pilot, tui)
                await pilot.press("S")
                await _settle(pilot, tui)
                assert writes(log) == [
                    ("POST", "/api/v1/plants/health/snapshot", None),
                    ("POST", "/api/v1/plants/sync", {"plant_id": None, "cluster_id": None}),
                    ("POST", "/api/v1/sync", {"hours": 24}),
                ]

        log: list = []
        _run(scenario())

    def test_remove_scheduler_job(self):
        """Uses a mock server: the real APScheduler is a process-wide singleton shared by all tests."""
        jobs = [
            {"id": "sensor_sync", "name": "Sensor data sync", "trigger": "interval", "next_run_time": None},
            {"id": "sensor_sync", "name": "Sensor data sync", "trigger": "interval", "next_run_time": None},
            {"id": "check_all", "name": "Check all clusters", "trigger": "cron", "next_run_time": None},
        ]
        seen: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            if request.method == "DELETE":
                return httpx.Response(200, json={"success": True})
            if request.url.path == "/api/v1/scheduler/jobs":
                return httpx.Response(200, json=jobs)
            return httpx.Response(200, json={})

        def factory(token):
            return IrrigationClient(base_url="http://mock", token="", transport=httpx.MockTransport(handler))

        async def scenario():
            tui = GreenhouseApp("http://mock", client_factory=factory, refresh_seconds=0)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("s")
                await _settle(pilot, tui)
                table = tui.screen.query_one("#jobs-table", DataTable)
                assert table.row_count == 2  # duplicate pending job collapsed
                table.move_cursor(row=1)
                await pilot.press("delete")
                await pilot.pause()
                await _confirm(pilot, tui)
                assert ("DELETE", "/api/v1/scheduler/jobs/check_all") in seen

        _run(scenario())


# ── Logic details: exact requests, cancel paths, errors, refresh, pagination ─


class TestRefill:
    def _table_app(self, body):
        from textual.app import App

        class TableApp(App):
            def compose(self):
                yield DataTable(id="t")

        async def scenario():
            app = TableApp()
            async with app.run_test() as pilot:
                table = app.query_one(DataTable)
                table.add_columns("v")
                await body(pilot, table)

        _run(scenario())

    def test_cursor_follows_record_across_reload(self):
        async def body(pilot, table):
            refill(table, [("1", ["a"]), ("2", ["b"]), ("3", ["c"])])
            table.move_cursor(row=2)
            assert selected_key(table) == "3"
            # Reload with a new row on top: the cursor stays on record 3, not row index 2.
            refill(table, [("0", ["new"]), ("1", ["a"]), ("2", ["b"]), ("3", ["c"])])
            assert selected_key(table) == "3"

        self._table_app(body)

    def test_cursor_clamps_when_record_disappears(self):
        async def body(pilot, table):
            refill(table, [("1", ["a"]), ("2", ["b"]), ("3", ["c"])])
            table.move_cursor(row=2)
            refill(table, [("1", ["a"]), ("2", ["b"])])
            assert selected_key(table) == "2"
            refill(table, [])
            assert selected_key(table) is None

        self._table_app(body)


class TestExactRequests:
    def _open(self, http, log, cluster_id, body, tab=None):
        async def scenario():
            tui = _app(http, log)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await tui.push_screen(ClusterScreen(cluster_id))
                await _settle(pilot, tui)
                if tab:
                    tui.screen.query_one("TabbedContent").active = tab
                    await _settle(pilot, tui)
                log.clear()
                await body(pilot, tui, tui.screen)

        _run(scenario())

    def test_irrigate_options_map_to_request_body(self, greenhouse):
        http, _ = greenhouse
        log: list = []

        async def body(pilot, tui, screen):
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

    def test_water_now_blank_minutes_uses_device_default(self, greenhouse):
        http, wiring = greenhouse
        log: list = []

        async def body(pilot, tui, screen):
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
    def test_cancel_never_writes(self, greenhouse, key, tab):
        http, wiring = greenhouse
        log: list = []

        async def body(pilot, tui, screen):
            await pilot.press(key)
            await pilot.pause()
            assert tui.screen is not screen  # a dialog opened
            await pilot.press("escape")
            await _settle(pilot, tui)
            assert tui.screen is screen
            assert writes(log) == []
            assert not wiring.irrigator.calls

        self._open(http, log, 1, body, tab)

    def test_check_cluster_after_confirm(self, greenhouse):
        http, _ = greenhouse
        log: list = []

        async def body(pilot, tui, screen):
            await pilot.press("c")
            await pilot.pause()
            await _confirm(pilot, tui)
            assert writes(log) == [("POST", "/api/v1/clusters/1/check", None)]

        self._open(http, log, 1, body)

    def test_new_and_edit_route_to_the_active_tab(self, greenhouse):
        http, _ = greenhouse
        log: list = []

        async def body(pilot, tui, screen):
            for tab in ("tab-decisions", "tab-history", "tab-charts", "tab-insights"):
                screen.query_one("TabbedContent").active = tab
                await _settle(pilot, tui)
                for key in ("n", "u", "delete"):
                    await pilot.press(key)
                    await pilot.pause()
                    assert tui.screen is screen, (tab, key)
            assert writes(log) == []

        self._open(http, log, 1, body)

    def test_existing_irrigator_cannot_be_added_twice(self, greenhouse):
        http, _ = greenhouse
        log: list = []

        async def body(pilot, tui, screen):
            await pilot.press("n")  # Overview tab, cluster already has a pump
            await pilot.pause()
            assert tui.screen is screen
            assert writes(log) == []

        self._open(http, log, 1, body)

    def test_alert_resolve_and_rescan(self, greenhouse):
        http, _ = greenhouse
        log: list = []

        async def scenario():
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

    def test_dashboard_check_all_and_sync(self, greenhouse):
        http, _ = greenhouse
        log: list = []

        async def scenario():
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


def _mock_app(handler, refresh_seconds: float = 0) -> GreenhouseApp:
    def factory(token):
        return IrrigationClient(base_url="http://mock", token="", transport=httpx.MockTransport(handler))

    return GreenhouseApp("http://mock", client_factory=factory, refresh_seconds=refresh_seconds)


class TestResilience:
    def test_server_error_is_toasted_not_raised(self):
        def handler(request):
            if request.url.path == "/api/v1/clusters":
                return httpx.Response(500, json={"detail": "database is locked"})
            return httpx.Response(200, json={})

        async def scenario():
            tui = _mock_app(handler)
            async with tui.run_test(size=SIZE, notifications=True) as pilot:
                await _settle(pilot, tui)
                messages = [n.message for n in tui._notifications]
                assert "database is locked" in messages
                assert "Cannot reach the server" in _text(tui.screen.query_one("#dashboard-empty", Static))

        _run(scenario())

    def test_empty_install_prompts_to_create_cluster(self):
        def handler(request):
            return httpx.Response(200, json=[] if request.url.path == "/api/v1/clusters" else {})

        async def scenario():
            tui = _mock_app(handler)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                assert "press n to create one" in _text(tui.screen.query_one("#dashboard-empty", Static))

        _run(scenario())

    def test_auto_refresh_polls_again(self):
        hits = []

        def handler(request):
            if request.url.path == "/api/v1/clusters":
                hits.append(1)
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={})

        async def scenario():
            tui = _mock_app(handler, refresh_seconds=0.2)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.pause(0.9)
                await _settle(pilot, tui)
                assert len(hits) >= 3

        _run(scenario())

    def test_r_reloads_active_screen(self):
        hits = []

        def handler(request):
            if request.url.path == "/api/v1/clusters":
                hits.append(1)
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={})

        async def scenario():
            tui = _mock_app(handler)
            async with tui.run_test(size=SIZE) as pilot:
                await _settle(pilot, tui)
                await pilot.press("r")
                await _settle(pilot, tui)
                assert len(hits) == 2

        _run(scenario())

    def test_activity_pagination_uses_cursor(self):
        requests = []

        def handler(request):
            if request.url.path != "/api/v1/activity":
                return httpx.Response(200, json={})
            params = dict(request.url.params)
            requests.append(params)
            item = {"timestamp": 1000, "source": "s", "entity_type": "cluster", "entity_id": 1, "severity": "info"}
            if "before" not in params:
                items = [{**item, "id": i, "code": f"c{i}", "message": "m"} for i in range(100)]
                return httpx.Response(200, json={"items": items, "next_cursor": 999})
            return httpx.Response(200, json={"items": [{**item, "id": 500, "code": "old", "message": "m"}]})

        async def scenario():
            tui = _mock_app(handler)
            async with tui.run_test(size=SIZE) as pilot:
                await pilot.press("l")
                await _settle(pilot, tui)
                table = tui.screen.query_one(DataTable)
                assert table.row_count == 100
                await pilot.press("n")
                await _settle(pilot, tui)
                assert table.row_count == 101
                assert requests[-1]["before"] == "999"
                await pilot.press("n")  # no cursor left → no request
                await _settle(pilot, tui)
                assert len([r for r in requests if "before" in r]) == 1

        _run(scenario())

    def test_login_failure_reprompts(self):
        application, engine = _make_stubbed_app(bypass_auth=False)

        def factory(token):
            client = IrrigationClient(base_url="http://testserver", token=token or "")
            client.http = TestClient(application, raise_server_exceptions=False)
            return client

        async def scenario():
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
