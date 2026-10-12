"""Characterization pins for TUI branches that the characterization suite left uncovered before a restructuring.

A branch-coverage run of the CLI and TUI suites (``--cov=greenhouse_cli --cov-branch``) reported these lines/branches
as missed in code that the TUI restructuring moved or re-dispatched:

- ``screens/cluster.py`` — decision panel (no decision; no ``interval_hours``; no ``reasons`` → ``reason`` text),
  forecast panel (unavailable; rain 6h; weather skip with and without a reason), sensor rows (water warning),
  insights panel ("nothing to flag"), and the ``action_edit`` / ``action_delete`` paths where nothing is selected or
  the cluster has no irrigator;
- ``screens/settings.py`` — vacation rows in the ``active`` and ``past`` states;
- ``model.summarize`` — readings without ``soil_moisture`` / ``temperature``;
- ``widgets.MetricChart`` — a dataset without points and a non-``start`` overlay event.

Each test drives the real app against the seeded in-memory server (frozen clock, offline weather). Only the client
methods named per test are replaced, to hand the screen a payload that reaches the branch. The pins are what the
code does today: rich segments ``[text, style]`` of panels, cell values of tables, toasts, and plain-text renders.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import asdict
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Unpack, cast

import pytest
from rich.console import Console
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static, TabbedContent

from cli.test_contract_tui import make_seeded_app, make_tui, screen_text, settle
from cli.tui_fixtures import tui_client_factory, writes
from golden import FROZEN_INSTANT, assert_golden, assert_golden_json
from greenhouse_cli.tui.model import summarize
from greenhouse_cli.tui.screens.cluster import ClusterScreen
from greenhouse_cli.tui.widgets import MetricChart

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator, Mapping
    from pathlib import Path

    import time_machine
    from fastapi.testclient import TestClient
    from rich.console import RenderableType
    from sqlalchemy.engine import Engine
    from textual.pilot import Pilot
    from textual.visual import VisualType

    from cli.test_contract_tui import NotifyOptions
    from cli.tui_fixtures import RequestLog
    from greenhouse_cli._client.transport import JSONObject
    from greenhouse_cli.client import IrrigationClient
    from greenhouse_cli.tui.app import GreenhouseApp

SIZE = (160, 50)
NOW = int(FROZEN_INSTANT.timestamp())


@pytest.fixture
def seeded(clean_env: Path, frozen_clock: time_machine.Traveller) -> Iterator[tuple[TestClient, Engine]]:
    """(http, engine) for a seeded app — clock frozen before ``seed_greenhouse`` runs."""
    del clean_env, frozen_clock  # side-effect fixtures (a hermetic environment; the clock is frozen)
    http, _, engine = make_seeded_app()
    yield http, engine
    engine.dispose()


def _overriding_factory(
    http: TestClient, overrides: Mapping[str, Callable[..., object]], log: list[RequestLog] | None = None
) -> Callable[[str | None], IrrigationClient]:
    """Client factory whose named methods are replaced by ``fake(real_method, *args, **kwargs)``."""
    base = tui_client_factory(http, log)

    def factory(token: str | None) -> IrrigationClient:
        client = base(token)
        for name, fake in overrides.items():
            real = getattr(client, name)

            def wrapper(
                *args: object,
                _fake: Callable[..., object] = fake,
                _real: Callable[..., object] = real,
                **kwargs: object,
            ) -> object:
                return _fake(_real, *args, **kwargs)

            setattr(client, name, wrapper)
        return client

    return factory


def _segments(renderable: VisualType, width: int = 100) -> list[list[str]]:
    """Rich segments of ``renderable`` as ``[text, style]`` pairs, adjacent equal styles merged."""
    console = Console(width=width, force_terminal=True, color_system="truecolor", no_color=False, legacy_windows=False)
    merged: list[list[str]] = []
    # The panels are only ever updated with rich renderables (never a Textual ``Visual``).
    for segment in console.render(cast("RenderableType", renderable)):
        style = str(segment.style) if segment.style else ""
        if merged and merged[-1][1] == style:
            merged[-1][0] += segment.text
        else:
            merged.append([segment.text, style])
    return merged


def _cell(value: object) -> object:
    if isinstance(value, Text):
        return {
            "plain": value.plain,
            "style": str(value.style),
            "spans": [[s.start, s.end, str(s.style)] for s in value.spans],
        }
    return value


def _rows(table: DataTable[object]) -> list[dict[str, object]]:
    return [{"key": row.key.value, "cells": [_cell(c) for c in table.get_row(row.key)]} for row in table.ordered_rows]


def _record_toasts(tui: GreenhouseApp) -> list[list[str]]:
    toasts: list[list[str]] = []
    original: Callable[..., None] = tui.notify

    def notify(message: str, *args: object, **kwargs: Unpack[NotifyOptions]) -> None:
        toasts.append([kwargs.get("severity", "information"), str(message)])
        return original(message, *args, **kwargs)

    tui.notify = notify  # type: ignore[method-assign]  # record toasts on this app instance only
    return toasts


def _run(coro: Coroutine[object, object, None]) -> None:
    asyncio.run(coro)


async def _open_cluster(pilot: Pilot[None], tui: GreenhouseApp, cluster_id: int) -> ClusterScreen:
    await settle(pilot, tui)
    screen = ClusterScreen(cluster_id)
    await tui.push_screen(screen)
    await settle(pilot, tui)
    return screen


# ── Overview: decision panel ─────────────────────────────────────────────

DECISION_VARIANTS: dict[str, dict[str, object] | None] = {
    "none": None,
    "no_interval_no_reasons": {
        "action": "irrigate",
        "duration_minutes": 4,
        "interval_hours": None,
        "confidence": 0.5,
        "reasons": [],
        "reason": "legacy free-text reason",
    },
    "no_duration_with_reasons": {
        "action": "skip",
        "duration_minutes": 0,
        "interval_hours": 0,
        "confidence": 0.9,
        "reasons": [{"severity": "warning", "icon": None, "message": "too wet", "code": "soil_saturated"}],
    },
}


def test_decision_panel_variants(seeded: tuple[TestClient, Engine]) -> None:
    """``#decision-panel`` for a missing decision, one without interval/reasons, one with reasons but no duration."""
    http, _ = seeded
    variant: dict[str, Any] = {}

    def status(real: Callable[[int], JSONObject], cid: int) -> JSONObject:
        payload = real(cid)
        payload["decision"] = copy.deepcopy(variant["decision"])
        return payload

    captured: dict[str, Any] = {}

    async def scenario() -> None:
        tui = make_tui(http, factory=_overriding_factory(http, {"status": status}))
        async with tui.run_test(size=SIZE) as pilot:
            variant["decision"] = DECISION_VARIANTS["none"]
            screen = await _open_cluster(pilot, tui, 1)
            for name, decision in DECISION_VARIANTS.items():
                variant["decision"] = decision
                screen.reload()
                await settle(pilot, tui)
                captured[name] = _segments(screen.query_one("#decision-panel", Static).content)

    _run(scenario())
    assert_golden_json("cli/wp2_gaps/decision_panel.json", captured)


# ── Overview: forecast panel ─────────────────────────────────────────────

FORECAST_VARIANTS = {
    "empty": {},
    "rain_and_skip_without_reason": {
        "hours_until_next": 5,
        "next_predicted_at": NOW + 5 * 3600,
        "projected_min_moisture": 31.5,
        "method": "trend",
        "confidence": 0.7,
        "precipitation_next_6h_mm": 2.4,
        "weather_skip": True,
        "weather_reason": None,
        "explanation": "rain expected",
    },
    "skip_with_reason_due_now": {
        "hours_until_next": 0,
        "method": "trend",
        "confidence": 0.4,
        "precipitation_next_6h_mm": None,
        "weather_skip": True,
        "weather_reason": "storm front",
    },
}


def test_forecast_panel_variants(seeded: tuple[TestClient, Engine]) -> None:
    """``#forecast-panel`` for an empty forecast, rain + skip without a reason, and skip with a reason."""
    http, _ = seeded
    variant: dict[str, Any] = {}
    captured: dict[str, Any] = {}

    async def scenario() -> None:
        tui = make_tui(http, factory=_overriding_factory(http, {"forecast": lambda _real, _cid: variant["f"]}))
        async with tui.run_test(size=SIZE) as pilot:
            variant["f"] = FORECAST_VARIANTS["empty"]
            screen = await _open_cluster(pilot, tui, 1)
            for name, forecast in FORECAST_VARIANTS.items():
                variant["f"] = copy.deepcopy(forecast)
                screen.reload()
                await settle(pilot, tui)
                captured[name] = _segments(screen.query_one("#forecast-panel", Static).content)

    _run(scenario())
    assert_golden_json("cli/wp2_gaps/forecast_panel.json", captured)


# ── Sensors tab: water warning ───────────────────────────────────────────


def test_sensor_rows_water_warning(seeded: tuple[TestClient, Engine]) -> None:
    """Sensor rows when a reading carries ``water_warning`` (with and without a battery state)."""
    http, _ = seeded

    def status(real: Callable[[int], JSONObject], cid: int) -> JSONObject:
        payload = real(cid)
        readings = [s["last_reading"] for s in payload["sensors"] if s.get("last_reading")]
        for reading, battery in zip(readings, ("low", None), strict=False):  # cluster 1 has two; dashboard asks all
            reading.update(water_warning=True, battery_state=battery)
        return payload

    captured: dict[str, Any] = {}

    async def scenario() -> None:
        tui = make_tui(http, factory=_overriding_factory(http, {"status": status}))
        async with tui.run_test(size=SIZE) as pilot:
            screen = await _open_cluster(pilot, tui, 1)
            captured["rows"] = _rows(screen.query_one("#sensors-table", DataTable))

    _run(scenario())
    assert_golden_json("cli/wp2_gaps/sensor_rows_water_warning.json", captured)


# ── Insights tab: nothing to flag ────────────────────────────────────────


def test_insights_panel_nothing_to_flag(seeded: tuple[TestClient, Engine]) -> None:
    """``#insights-panel`` with no insights and nobody needing water."""
    http, _ = seeded
    overrides: dict[str, Callable[..., object]] = {
        "insights": lambda _real, _cid: {"insights": []},
        "monitor": lambda _real, _cid: {"needs_water": []},
    }
    captured: dict[str, Any] = {}

    async def scenario() -> None:
        tui = make_tui(http, factory=_overriding_factory(http, overrides))
        async with tui.run_test(size=SIZE) as pilot:
            screen = await _open_cluster(pilot, tui, 1)
            screen.query_one("TabbedContent", TabbedContent).active = "tab-insights"
            await settle(pilot, tui)
            captured["insights"] = _segments(screen.query_one("#insights-panel", Static).content)

    _run(scenario())
    assert_golden_json("cli/wp2_gaps/insights_nothing_to_flag.json", captured)


# ── CRUD keys with nothing selected / no irrigator ───────────────────────


def _create_empty_cluster(http: TestClient) -> int:
    """Create a cluster with no plants, sensors, pump or windows; return its id."""
    resp = http.post("/api/v1/clusters", json={"name": "Empty", "environment": "indoor", "location": None})
    assert resp.status_code == HTTPStatus.CREATED, resp.text
    cluster_id: int = resp.json()["id"]
    return cluster_id


async def _press_on_tab(
    pilot: Pilot[None],
    tui: GreenhouseApp,
    screen: ClusterScreen,
    tab_key: tuple[str, str],
    recorded: tuple[list[list[str]], list[RequestLog]],
) -> dict[str, object]:
    """Press one key on the active tab and record its toasts, dialog and writes; a dialog is escaped."""
    tab, key = tab_key
    toasts, log = recorded
    start_toasts, start_log = len(toasts), len(log)
    await pilot.press(key)
    await settle(pilot, tui)
    dialog = type(tui.screen).__name__ if tui.screen is not screen else None
    capture: dict[str, object] = {
        "tab": tab,
        "key": key,
        "toasts": toasts[start_toasts:],
        "dialog": dialog,
        "writes": [list(w) for w in writes(log[start_log:])],
    }
    if dialog:
        await pilot.press("escape")
        await settle(pilot, tui)
    return capture


def test_edit_and_delete_with_nothing_selected(seeded: tuple[TestClient, Engine]) -> None:
    """``u`` / ``delete`` on every CRUD tab of an empty cluster: the toast, no dialog, no write."""
    http, _ = seeded
    empty_id = _create_empty_cluster(http)
    log: list[RequestLog] = []
    captured: list[dict[str, object]] = []

    async def scenario() -> None:
        tui = make_tui(http, log, factory=None)
        toasts = _record_toasts(tui)
        async with tui.run_test(size=SIZE) as pilot:
            screen = await _open_cluster(pilot, tui, empty_id)
            for tab in ("tab-plants", "tab-sensors", "tab-windows", "tab-overview", "tab-config"):
                screen.query_one("TabbedContent", TabbedContent).active = tab
                await settle(pilot, tui)
                captured.extend(
                    [await _press_on_tab(pilot, tui, screen, (tab, key), (toasts, log)) for key in ("u", "delete")]
                )

    _run(scenario())
    assert_golden_json("cli/wp2_gaps/crud_nothing_selected.json", captured)


# ── Settings: vacation states ────────────────────────────────────────────


def test_vacation_rows_states(seeded: tuple[TestClient, Engine]) -> None:
    """Vacation rows in the active, past and upcoming states."""
    http, _ = seeded
    vacation = {
        "items": [
            {"id": 7, "starts_at": NOW - 3600, "ends_at": NOW + 3600, "contact_email": "a@example.com", "notes": "on"},
            {"id": 8, "starts_at": NOW - 9 * 86400, "ends_at": NOW - 2 * 86400, "contact_email": None, "notes": None},
            {
                "id": 9,
                "starts_at": NOW + 3 * 86400,
                "ends_at": NOW + 5 * 86400,
                "contact_email": None,
                "notes": "later",
            },
        ],
        "active": {"id": 7},
    }
    captured: dict[str, Any] = {}

    async def scenario() -> None:
        factory = _overriding_factory(http, {"list_vacation": lambda _real: copy.deepcopy(vacation)})
        tui = make_tui(http, factory=factory)
        async with tui.run_test(size=SIZE) as pilot:
            await settle(pilot, tui)
            await pilot.press("o")
            await settle(pilot, tui)
            captured["rows"] = _rows(tui.screen.query_one("#vacation-table", DataTable))

    _run(scenario())
    assert_golden_json("cli/wp2_gaps/vacation_rows.json", captured)


# ── model.summarize: partial readings ────────────────────────────────────


def test_summarize_with_partial_readings() -> None:
    """Readings lacking soil moisture or temperature still count toward the means/newest stamp they carry."""
    status = {
        "cluster": {"id": 5, "name": "Partial", "environment": "outdoor", "location": "roof"},
        "decision": None,
        "plants": [
            {"id": 1, "species": "Aloe vera", "category": "succulent"},
            {"id": 2, "species": "Ficus lyrata", "category": None},
        ],
        "sensors": [
            {"plant_id": 1, "last_reading": {"timestamp": 100, "temperature": 20.0, "env_humidity": 40, "light": 900}},
            {"plant_id": 2, "last_reading": {"timestamp": 300, "soil_moisture": 33.0, "env_humidity": 60}},
            {"plant_id": None, "last_reading": {"timestamp": 200, "soil_moisture": 41.0, "temperature": 24.0}},
            {"plant_id": 1, "last_reading": None},
        ],
        "irrigator": None,
    }
    chart = {"threshold": {"min": 30, "max": 60}, "datasets": [{"points": [[1, 40.0], [2, 35.0]]}]}
    assert_golden_json("cli/wp2_gaps/summarize_partial.json", asdict(summarize(status, chart)))


# ── MetricChart: empty series, non-start overlay event ───────────────────


class _ChartApp(App[None]):
    def compose(self) -> ComposeResult:
        yield MetricChart(id="chart")


@pytest.mark.usefixtures("clean_env", "frozen_clock")
def test_metric_chart_skips_empty_series_and_non_start_events() -> None:
    """``show_payload`` with an empty dataset next to a real one; ``show_overlay`` with a ``stop`` event."""
    payload = {
        "datasets": [
            {"sensor_name": "empty", "points": []},
            {"sensor_name": "probe", "points": [[NOW - 7200, 40.0], [NOW - 3600, 38.0], [NOW, 36.5]]},
        ],
        "threshold": {"min": 30, "max": None},
        "events": [{"action": "start", "timestamp": NOW - 5400}, {"action": "stop", "timestamp": NOW - 1800}],
    }
    overlay = {
        "datasets": [
            {"metric": "soil", "points": [[NOW - 7200, 50.0], [NOW, 45.0]]},
            {"metric": "light", "original_max": 20000, "points": []},
        ],
        "events": [{"action": "stop", "timestamp": NOW - 3600}, {"action": "start", "timestamp": NOW - 1800}],
    }
    captured: dict[str, str] = {}

    async def scenario() -> None:
        app = _ChartApp()
        async with app.run_test(size=(100, 30)) as pilot:
            chart = app.query_one(MetricChart)
            chart.show_payload(payload, "soil_moisture", 6)
            await pilot.pause()
            await settle(pilot, app)
            captured["payload"] = screen_text(app)
            chart.show_overlay(overlay, 6)
            await pilot.pause()
            await settle(pilot, app)
            captured["overlay"] = screen_text(app)

    _run(scenario())
    assert_golden("cli/wp2_gaps/metric_chart_payload.txt", captured["payload"])
    assert_golden("cli/wp2_gaps/metric_chart_overlay.txt", captured["overlay"])
