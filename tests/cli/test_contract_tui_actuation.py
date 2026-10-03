"""Characterization of the TUI's actuating keys (gap G12).

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

import pytest

from cli.test_contract_tui import make_seeded_app, make_tui, settle
from cli.tui_fixtures import writes
from golden import assert_golden_json
from greenhouse_cli.tui.screens.cluster import ClusterScreen
from greenhouse_cli.tui.screens.dashboard import DashboardScreen
from greenhouse_cli.tui.screens.forms import FormScreen
from greenhouse_cli.tui.screens.modals import ConfirmScreen, IrrigateScreen, WaterNowScreen

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


def _record_toasts(tui) -> list[tuple[str, str]]:
    """Wrap ``app.notify`` (every screen's ``notify`` delegates to it) to record (severity, message)."""
    toasts: list[tuple[str, str]] = []
    original = tui.notify

    def notify(message, *args, **kwargs):
        toasts.append((kwargs.get("severity", "information"), str(message)))
        return original(message, *args, **kwargs)

    tui.notify = notify
    return toasts


def _dialog(screen) -> dict | None:
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

    def __init__(self, pilot, tui, log: list, label: str) -> None:
        self.pilot, self.tui, self.log, self.label = pilot, tui, log, label
        self.toasts = _record_toasts(tui)
        self.rows: list[dict] = []

    async def tab(self, tab: str) -> None:
        self.tui.screen.query_one("TabbedContent").active = tab
        await settle(self.pilot, self.tui)

    async def key(self, key: str, fill: dict | None = None, note: str | None = None) -> dict:
        pilot, tui = self.pilot, self.tui
        base = tui.screen
        tab = base.active_tab if isinstance(base, ClusterScreen) else None
        start_log, start_toasts = len(self.log), len(self.toasts)
        await pilot.press(key)
        await settle(pilot, tui)
        dialog_screen = tui.screen if tui.screen is not base else None
        row = {
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
        if isinstance(dialog_screen, ConfirmScreen):
            await pilot.click("#confirm")
        elif isinstance(dialog_screen, FormScreen):
            for name, value in (fill or {}).items():
                dialog_screen.query_one(f"#field-{name}").value = value
            await pilot.click("#submit")
        elif isinstance(dialog_screen, IrrigateScreen):
            await pilot.click("#run")  # defaults: dry run on, sync on, no force
        elif isinstance(dialog_screen, WaterNowScreen):
            await pilot.click("#start")  # blank minutes = device default
        await settle(pilot, tui)
        assert tui.screen is not dialog_screen or dialog_screen is None, f"{self.label}/{key}: dialog still open"
        row["writes"] = [list(w) for w in writes(self.log[start_log:])]
        # The only normalization: ``E`` toasts the export path, which lives in pytest's random tmp cwd.
        cwd = str(Path.cwd())
        row["toasts"] = [[sev, msg.replace(cwd, "<CWD>")] for sev, msg in self.toasts[start_toasts:]]
        self.rows.append(row)
        return row


async def _dashboard(http, rows: list) -> None:
    log: list = []
    tui = make_tui(http, log)
    async with tui.run_test(size=SIZE) as pilot:
        await settle(pilot, tui)
        rec = _Recorder(pilot, tui, log, "dashboard")
        await rec.key("c")
        await rec.key("S")
        await rec.key("n", fill={"name": "Kitchen", "location": "sill"})
        await rec.key("X")
        rows += rec.rows


async def _cluster(http, rows: list) -> None:
    log: list = []
    tui = make_tui(http, log)
    async with tui.run_test(size=SIZE) as pilot:
        await settle(pilot, tui)
        await tui.push_screen(ClusterScreen(1))
        await settle(pilot, tui)
        rec = _Recorder(pilot, tui, log, "cluster")
        for key in ("i", "w", "x", "c"):
            await rec.key(key)
        await rec.key("n", note="cluster already has an irrigator")
        await rec.key("u")
        await rec.key("L", fill={"minutes": "4", "notes": "watering can"})
        await rec.key("P")
        await rec.key("E", note="GET stats export only; CSV written to cwd")
        await rec.key("e")

        await rec.tab("tab-plants")
        await rec.key("n", fill={"species": "Ficus lyrata", "category": "tropical"})
        await rec.key("u")
        await rec.key("M", fill={"target_cluster_id": 3})
        await rec.key("delete")
        await rec.key("m", note="toggles plant chart, read-only")

        await rec.tab("tab-sensors")
        await rec.key("n", fill={"tuya_device_id": "fake_tuya_device_aabbccdd", "name": "Spare probe", "type": "x"})
        await rec.key("u")
        await rec.key("delete")

        await rec.tab("tab-windows")
        await rec.key("delete", note="empty table: no selection")
        await rec.key("u", note="empty table: no selection")
        await rec.key("n", fill={"start_hour": "6", "end_hour": "9", "label": "morning"})
        await rec.key("u")
        await rec.key("delete")

        for tab in ("tab-charts", "tab-insights", "tab-decisions", "tab-history"):
            await rec.tab(tab)
            for key in ("n", "u", "delete"):
                await rec.key(key)
        await rec.tab("tab-charts")
        for key in ("m", "]", "["):
            await rec.key(key)

        await rec.tab("tab-config")
        await rec.key("u")

        await rec.tab("tab-overview")
        await rec.key("delete")
        await rec.key("w", note="no irrigator after detach")
        await rec.key("n", fill={"tuya_device_id": "fake_tuya_device_00112233", "name": "New pump", "type": "t"})
        await rec.key("D")
        assert isinstance(tui.screen, DashboardScreen)
        rows += rec.rows


async def _alerts(http, rows: list) -> None:
    log: list = []
    tui = make_tui(http, log)
    async with tui.run_test(size=SIZE) as pilot:
        await pilot.press("a")
        await settle(pilot, tui)
        rec = _Recorder(pilot, tui, log, "alerts")
        for key in ("k", "v", "y", "f"):
            await rec.key(key)
        rows += rec.rows


async def _system(http, rows: list) -> None:
    log: list = []
    tui = make_tui(http, log)
    async with tui.run_test(size=SIZE) as pilot:
        await pilot.press("s")
        await settle(pilot, tui)
        rec = _Recorder(pilot, tui, log, "system")
        await rec.key("delete", note="cursor on a built-in job")
        await rec.key("p", note="pause (scheduler active)")
        await rec.key("p", note="resume (scheduler paused)")
        for key in ("S", "P", "H"):
            await rec.key(key)
        rows += rec.rows


async def _settings(http, rows: list) -> None:
    log: list = []
    tui = make_tui(http, log)
    async with tui.run_test(size=SIZE) as pilot:
        await pilot.press("o")
        await settle(pilot, tui)
        rec = _Recorder(pilot, tui, log, "settings")
        await rec.key("u", note="no vacation windows yet")
        await rec.key("delete", note="no vacation windows yet")
        await rec.key("p")
        await rec.key("g")
        await rec.key("n", fill={"starts_at": "2030-07-01 08:00", "ends_at": "2030-07-15 20:00", "notes": "sea"})
        await rec.key("u")
        await rec.key("delete")
        await rec.key("O")
        rows += rec.rows


def test_actuating_keys_golden(seeded):
    """Key → dialog → exact mutating requests (+ toasts) for every screen == golden."""
    http, _ = seeded
    rows: list[dict] = []
    for session in (_dashboard, _cluster, _alerts, _system, _settings):
        _run(session(http, rows))
    assert_golden_json("tui/actuation.json", rows)


def test_dialog_kind_per_actuating_key(seeded):
    """The headline table of the golden, asserted directly so a diff names the key that changed."""
    http, _ = seeded
    rows: list[dict] = []
    _run(_dashboard(http, rows))
    _run(_system(http, rows))
    kinds = {(r["screen"], r["key"], r.get("note")): r["dialog"] for r in rows}
    assert kinds == {
        ("dashboard", "c", None): "ConfirmScreen",
        ("dashboard", "S", None): None,
        ("dashboard", "n", None): "FormScreen",
        ("dashboard", "X", None): "ConfirmScreen",
        ("system", "delete", "cursor on a built-in job"): None,
        ("system", "p", "pause (scheduler active)"): "ConfirmScreen",
        ("system", "p", "resume (scheduler paused)"): None,
        ("system", "S", None): None,
        ("system", "P", None): None,
        ("system", "H", None): None,
    }
