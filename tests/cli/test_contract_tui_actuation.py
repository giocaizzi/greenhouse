"""Characterization of the TUI's actuating keys.

- ``tests/golden/tui/actuation.json`` — for every write-producing key on every
  screen (and every contextual tab of the cluster screen): which dialog opens
  (``ConfirmScreen`` / ``IrrigateScreen`` / ``WaterNowScreen`` / ``FormScreen`` /
  none), the dialog's text, and — after confirming / submitting — the exact
  mutating HTTP requests (method, path, JSON body) plus the toasts raised.
  This pins the CURRENT behavior, including the keys that bypass
  ``ConfirmScreen``: ``i`` / ``w`` open their own dialogs; ``S`` sync, ``P``
  plant sync, ``H`` snapshot, alert ``k`` / ``v`` / ``y`` and scheduler resume
  (``p`` while paused) run with no dialog at all.
G13 / G14 / 401 live in ``test_contract_tui_runtime.py``.

Clock frozen before seeding, ``TZ=UTC``, offline weather, animations off.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Unpack, cast

import pytest
from textual.widgets import TabbedContent

from cli.test_contract_tui import make_seeded_app, make_tui, settle
from cli.tui_fixtures import writes
from golden import assert_golden_json
from greenhouse_cli.tui.screens.cluster import ClusterScreen
from greenhouse_cli.tui.screens.dashboard import DashboardScreen
from greenhouse_cli.tui.screens.forms import FormScreen
from greenhouse_cli.tui.screens.modals import ConfirmScreen, IrrigateScreen, WaterNowScreen

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator

    import time_machine
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Engine
    from textual.pilot import Pilot
    from textual.screen import Screen

    from cli.test_contract_tui import NotifyOptions
    from cli.tui_fixtures import RequestLog
    from greenhouse_cli.tui.app import GreenhouseApp

Row = dict[str, object]  # one golden row: screen, tab, key, dialog, writes, toasts
FormFill = dict[str, str | int]


class _ValueField(Protocol):
    """A form field widget (``Input`` / ``Select`` / ``Checkbox``) whose ``value`` the recorder sets."""

    value: object


SIZE = (160, 50)
# Auto-refresh period for the G14 tests: short enough to keep them quick, long enough for a reload to finish.
REFRESH = 0.5


@pytest.fixture
def seeded(clean_env: Path, frozen_clock: time_machine.Traveller) -> Iterator[tuple[TestClient, Engine]]:
    """(http, engine) for a seeded app — clock frozen before ``seed_greenhouse`` runs."""
    del clean_env, frozen_clock  # side-effect fixtures (a hermetic environment; the clock is frozen)
    http, _, engine = make_seeded_app()
    yield http, engine
    engine.dispose()


def _run(coro: Coroutine[object, object, None]) -> None:
    asyncio.run(coro)


def _record_toasts(tui: GreenhouseApp, mp: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Wrap ``app.notify`` (every screen's ``notify`` delegates to it) to record (severity, message)."""
    toasts: list[tuple[str, str]] = []
    original: Callable[..., None] = tui.notify

    def notify(message: str, *args: object, **kwargs: Unpack[NotifyOptions]) -> None:
        toasts.append((kwargs.get("severity", "information"), str(message)))
        return original(message, *args, **kwargs)

    mp.setattr(tui, "notify", notify)
    return toasts


def _dialog(screen: Screen[object]) -> dict[str, object] | None:
    if isinstance(screen, ConfirmScreen):
        return {"message": screen.message, "confirm_label": screen.confirm_label, "danger": screen.danger}
    if isinstance(screen, FormScreen):
        return {
            "title": screen.form_title,
            "fields": [f.name for f in screen.fields],
            "prefilled": {f.name: f.value for f in screen.fields if f.value is not None},
            "submit_label": screen.submit_label,
            "note": screen.note,
        }
    if isinstance(screen, IrrigateScreen):
        return {"cluster_name": screen.cluster_name}
    if isinstance(screen, WaterNowScreen):
        return {"irrigator_name": screen.irrigator_name}
    return None


class _Recorder:
    """Drive one key at a time and record dialog + mutating requests + toasts."""

    def __init__(
        self, pilot: Pilot[None], tui: GreenhouseApp, log: list[RequestLog], label: str, mp: pytest.MonkeyPatch
    ) -> None:
        self.pilot, self.tui, self.log, self.label = pilot, tui, log, label
        self.toasts = _record_toasts(tui, mp)
        self.rows: list[Row] = []

    async def tab(self, tab: str) -> None:
        self.tui.screen.query_one("TabbedContent", TabbedContent).active = tab
        await settle(self.pilot, self.tui)

    async def key(self, key: str, fill: FormFill | None = None, note: str | None = None) -> Row:
        pilot, tui = self.pilot, self.tui
        base = tui.screen
        tab = base.active_tab if isinstance(base, ClusterScreen) else None
        start_log, start_toasts = len(self.log), len(self.toasts)
        await pilot.press(key)
        await settle(pilot, tui)
        dialog_screen = tui.screen if tui.screen is not base else None
        row: Row = {
            "screen": self.label,
            "tab": tab,
            "key": key,
            "dialog": type(dialog_screen).__name__ if dialog_screen else None,
            "dialog_detail": _dialog(dialog_screen) if dialog_screen else None,
        }
        if fill:
            row["filled"] = fill
        if note:
            row["note"] = note
        await self._accept(dialog_screen, fill)
        await settle(pilot, tui)
        assert tui.screen is not dialog_screen or dialog_screen is None, f"{self.label}/{key}: dialog still open"
        row["writes"] = [list(w) for w in writes(self.log[start_log:])]
        # The only normalization: ``E`` toasts the export path, which lives in pytest's random tmp cwd.
        cwd = str(Path.cwd())
        row["toasts"] = [[sev, msg.replace(cwd, "<CWD>")] for sev, msg in self.toasts[start_toasts:]]
        self.rows.append(row)
        return row

    async def _accept(self, dialog_screen: Screen[object] | None, fill: FormFill | None) -> None:
        """Accept whatever dialog the key opened, with its default choices (forms get ``fill``)."""
        pilot = self.pilot
        if isinstance(dialog_screen, ConfirmScreen):
            await pilot.click("#confirm")
        elif isinstance(dialog_screen, FormScreen):
            for name, value in (fill or {}).items():
                cast("_ValueField", dialog_screen.query_one(f"#field-{name}")).value = value
            await pilot.click("#submit")
        elif isinstance(dialog_screen, IrrigateScreen):
            await pilot.click("#run")  # defaults: dry run on, sync on, no force
        elif isinstance(dialog_screen, WaterNowScreen):
            await pilot.click("#start")  # blank minutes = device default


async def _dashboard(http: TestClient, rows: list[Row], mp: pytest.MonkeyPatch) -> None:
    log: list[RequestLog] = []
    tui = make_tui(http, log)
    async with tui.run_test(size=SIZE) as pilot:
        await settle(pilot, tui)
        rec = _Recorder(pilot, tui, log, "dashboard", mp)
        await rec.key("c")
        await rec.key("S")
        await rec.key("n", fill={"name": "Kitchen", "location": "sill"})
        await rec.key("X")
        rows += rec.rows


async def _cluster(http: TestClient, rows: list[Row], mp: pytest.MonkeyPatch) -> None:
    log: list[RequestLog] = []
    tui = make_tui(http, log)
    async with tui.run_test(size=SIZE) as pilot:
        await settle(pilot, tui)
        await tui.push_screen(ClusterScreen(1))
        await settle(pilot, tui)
        rec = _Recorder(pilot, tui, log, "cluster", mp)
        await _cluster_overview_keys(rec)
        await _cluster_plant_keys(rec)
        await _cluster_sensor_keys(rec)
        await _cluster_window_keys(rec)
        await _cluster_chart_and_config_tab_keys(rec)
        await _cluster_detach_and_delete(rec)
        assert isinstance(tui.screen, DashboardScreen)
        rows += rec.rows


async def _cluster_overview_keys(rec: _Recorder) -> None:
    for key in ("i", "w", "x", "c"):
        await rec.key(key)
    await rec.key("n", note="cluster already has an irrigator")
    await rec.key("u")
    await rec.key("L", fill={"minutes": "4", "notes": "watering can"})
    await rec.key("P")
    await rec.key("E", note="GET stats export only; CSV written to cwd")
    await rec.key("e")


async def _cluster_plant_keys(rec: _Recorder) -> None:
    await rec.tab("tab-plants")
    await rec.key("n", fill={"species": "Ficus lyrata", "category": "tropical"})
    await rec.key("u")
    await rec.key("M", fill={"target_cluster_id": 3})
    await rec.key("delete")
    await rec.key("m", note="toggles plant chart, read-only")


async def _cluster_sensor_keys(rec: _Recorder) -> None:
    await rec.tab("tab-sensors")
    await rec.key(
        "n", fill={"tuya_device_id": "fake_tuya_device_aabbccdd", "name": "Spare probe", "type": "tuya.tr301z"}
    )
    await rec.key("u")
    await rec.key("delete")


async def _cluster_window_keys(rec: _Recorder) -> None:
    await rec.tab("tab-windows")
    await rec.key("delete", note="empty table: no selection")
    await rec.key("u", note="empty table: no selection")
    await rec.key("n", fill={"start_hour": "6", "end_hour": "9", "label": "morning"})
    await rec.key("u")
    await rec.key("delete")


async def _cluster_chart_and_config_tab_keys(rec: _Recorder) -> None:
    for tab in ("tab-charts", "tab-insights", "tab-decisions", "tab-history"):
        await rec.tab(tab)
        for key in ("n", "u", "delete"):
            await rec.key(key)
    await rec.tab("tab-charts")
    for key in ("m", "]", "["):
        await rec.key(key)
    await rec.tab("tab-config")
    await rec.key("u")


async def _cluster_detach_and_delete(rec: _Recorder) -> None:
    await rec.tab("tab-overview")
    await rec.key("delete")
    await rec.key("w", note="no irrigator after detach")
    await rec.key(
        "n", fill={"tuya_device_id": "fake_tuya_device_00112233", "name": "New pump", "type": "rainpoint.ik10pw"}
    )
    await rec.key("D")


async def _alerts(http: TestClient, rows: list[Row], mp: pytest.MonkeyPatch) -> None:
    log: list[RequestLog] = []
    tui = make_tui(http, log)
    async with tui.run_test(size=SIZE) as pilot:
        await pilot.press("a")
        await settle(pilot, tui)
        rec = _Recorder(pilot, tui, log, "alerts", mp)
        for key in ("k", "v", "y", "f"):
            await rec.key(key)
        rows += rec.rows


async def _system(http: TestClient, rows: list[Row], mp: pytest.MonkeyPatch) -> None:
    log: list[RequestLog] = []
    tui = make_tui(http, log)
    async with tui.run_test(size=SIZE) as pilot:
        await pilot.press("s")
        await settle(pilot, tui)
        rec = _Recorder(pilot, tui, log, "system", mp)
        await rec.key("delete", note="cursor on a built-in job")
        await rec.key("p", note="pause (scheduler active)")
        await rec.key("p", note="resume (scheduler paused)")
        for key in ("S", "P", "H"):
            await rec.key(key)
        rows += rec.rows


async def _settings(http: TestClient, rows: list[Row], mp: pytest.MonkeyPatch) -> None:
    log: list[RequestLog] = []
    tui = make_tui(http, log)
    async with tui.run_test(size=SIZE) as pilot:
        await pilot.press("o")
        await settle(pilot, tui)
        rec = _Recorder(pilot, tui, log, "settings", mp)
        await rec.key("u", note="no vacation windows yet")
        await rec.key("delete", note="no vacation windows yet")
        await rec.key("p")
        await rec.key("g")
        await rec.key("n", fill={"starts_at": "2030-07-01 08:00", "ends_at": "2030-07-15 20:00", "notes": "sea"})
        await rec.key("u")
        await rec.key("delete")
        await rec.key("O")
        rows += rec.rows


def test_actuating_keys_golden(seeded: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch) -> None:
    """Key → dialog → exact mutating requests (+ toasts) for every screen == golden."""
    http, _ = seeded
    rows: list[Row] = []
    for session in (_dashboard, _cluster, _alerts, _system, _settings):
        _run(session(http, rows, monkeypatch))
    assert_golden_json("tui/actuation.json", rows)


def test_dialog_kind_per_actuating_key(seeded: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch) -> None:
    """The headline table of the golden, asserted directly so a diff names the key that changed."""
    http, _ = seeded
    rows: list[Row] = []
    _run(_dashboard(http, rows, monkeypatch))
    _run(_system(http, rows, monkeypatch))
    kinds = {(r["screen"], r["key"], r.get("note")): r["dialog"] for r in rows}
    assert kinds == {
        ("dashboard", "c", None): "ConfirmScreen",
        ("dashboard", "S", None): None,
        ("dashboard", "n", None): "FormScreen",
        ("dashboard", "X", None): "ConfirmScreen",
        ("system", "delete", "cursor on a built-in job"): None,
        ("system", "p", "pause (scheduler active)"): "ConfirmScreen",
        ("system", "p", "resume (scheduler paused)"): "ConfirmScreen",
        ("system", "S", None): None,
        ("system", "P", None): "ConfirmScreen",
        ("system", "H", None): None,
    }
