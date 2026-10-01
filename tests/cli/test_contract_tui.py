"""Characterization goldens for the Textual TUI — static surface and sprites (gap G12).

Pins *what the TUI declares today* so a behavior-preserving refactor of
``greenhouse_cli.tui`` can prove it changed nothing a user (or ``app.tcss``)
depends on:

- ``tests/golden/tui/surface.json`` — per class (the app and every Screen /
  ModalScreen / Widget subclass defined under ``tui/``): normalized
  ``BINDINGS`` in declaration order, ``MODES`` / ``SCREENS`` / ``CSS_PATH`` /
  other upper-case class attributes, a hash of ``DEFAULT_CSS``, the set of
  ``action_*`` / ``on_*`` / ``_on_*`` / ``watch_*`` methods, ``@on(...)``
  handlers' message types + selectors; plus module-level constants and every
  form field spec in ``resources.py``.
- ``tests/golden/tui/sprites.json`` — the mood → sprite-frame mapping (palette
  keyed pixel rows) for every category, mood and animation frame.

This module also hosts the small kit shared by ``test_contract_tui_*.py``
(seeded app builder, TUI factory, plain-text screen export, and the ``settle`` /
``wait_until`` helpers that wait for real conditions instead of timing).
"""

from __future__ import annotations

import ast
import asyncio
import enum
import hashlib
import importlib
import inspect
import io
import pkgutil
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from rich.console import Console
from textual.app import App
from textual.binding import Binding
from textual.dom import DOMNode
from textual.widgets import DataTable
from textual.worker import WorkerCancelled

from cli.tui_fixtures import seed_greenhouse, tui_client_factory
from golden import assert_golden_json, install_offline_weather
from greenhouse_cli import tui as tui_pkg
from greenhouse_cli.tui import resources, sprites
from greenhouse_cli.tui.app import GreenhouseApp
from server.conftest import _make_stubbed_app

# ── Shared kit (imported by test_contract_tui_screens / _actuation / _runtime) ─

SERVER_URL = "http://testserver"


def make_seeded_app(*, bypass_auth: bool = True):
    """Seeded stubbed server app with offline weather.

    Call it with the clock already frozen: ``seed_greenhouse`` stamps every
    reading / event / activity row with ``time.time()``.

    Returns:
        ``(http, application, engine)`` — dispose the engine when done.
    """
    application, engine = _make_stubbed_app(bypass_auth=bypass_auth)
    install_offline_weather(application)
    http = TestClient(application, raise_server_exceptions=False)
    if bypass_auth:
        seed_greenhouse(http, engine)
    return http, application, engine


def make_tui(http: TestClient, log: list | None = None, refresh_seconds: float = 0, factory=None) -> GreenhouseApp:
    """A GreenhouseApp with sprite animation off (what ``greenhouse tui --no-animation`` passes)."""
    return GreenhouseApp(
        SERVER_URL,
        client_factory=factory or tui_client_factory(http, log),
        refresh_seconds=refresh_seconds,
        animations=False,
    )


def screen_text(app: App) -> str:
    """Plain-text rendering of the current screen (incl. background screens under a modal).

    Mirrors ``App.export_screenshot`` but records text instead of SVG; trailing
    spaces are stripped per line (whitespace only — no content is normalized).
    """
    width, height = app.size
    console = Console(
        width=width,
        height=height,
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        record=True,
        legacy_windows=False,
        safe_box=False,
    )
    update = app.screen._compositor.render_update(full=True, screen_stack=app._background_screens)
    console.print(update)
    lines = [line.rstrip() for line in console.export_text(styles=False).splitlines()]
    return "\n".join(lines).rstrip("\n") + "\n"


# ── Synchronization (shared by test_contract_tui_*) ─────────────────────────


def _pending_tui_work(tui: App) -> list[str]:
    """Everything the app still has to process: unfinished workers, queued messages, deferred callbacks,
    running or scheduled animations (e.g. the 0.3 s slide of the active-tab underline), an open update batch
    (``switch_mode`` → ``delay_update``), layout/repaint the visible screen still owes, and ``DataTable`` rows
    whose column widths are only measured on the table's next idle.

    Looks at *every* mode's screen stack, not just the active screen, so a worker or a message still
    in flight on a suspended screen (e.g. the dashboard after ``switch_mode``) counts as pending.
    """
    pending = [f"worker {w.description!r} ({w.state.name})" for w in tui.workers if not w.is_finished]
    nodes: list[Any] = [tui]
    for stack in tui._screen_stacks.values():
        for screen in stack:
            nodes.extend(screen.walk_children(with_self=True))
    for node in nodes:
        if not node._message_queue.empty() or node._next_callbacks:
            pending.append(f"messages queued on {node!r}")
    animator = tui.animator
    pending += [f"animation of {attr!r}" for _, attr in [*animator._animations, *animator._scheduled]]
    if tui._batch_count:
        pending.append("update batch still open")
    screen = tui.screen
    if screen._callbacks:  # call_after_refresh work; only the active screen refreshes
        pending.append(f"after-refresh callbacks on {screen!r}")
    if screen._layout_required or screen._repaint_required or screen._recompose_required or screen._dirty_widgets:
        pending.append(f"layout/repaint pending on {screen!r}")
    for visible in [*tui._background_screens, screen]:  # what ``screen_text`` renders
        for table in visible.query(DataTable):
            if table._require_update_dimensions or table._updated_cells:
                pending.append(f"column widths not yet measured on {table!r}")
                table.check_idle()  # Textual measures them on the table's next idle; prompt that idle
    return pending


async def settle(pilot, tui: App, timeout: float = 60.0) -> None:
    """Wait until the TUI has finished reacting: no worker unfinished, no message or callback queued
    anywhere, on two consecutive passes. Fails with what is still pending if that never happens.

    Stricter than ``test_tui._settle``: a key press travels app → focused widget → back up to the app's
    bindings through several message queues, and a busy CPU can leave part of that trip (or the worker it
    starts, the tab-underline animation, or a table's column measurement) still pending after Textual's
    ``pilot.pause()`` idle heuristic returns.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    quiet_passes = 0
    while quiet_passes < 2:
        await pilot.pause()
        try:
            await tui.workers.wait_for_complete()
        except WorkerCancelled:
            pass  # an exclusive worker legitimately cancels the run it supersedes
        await tui.animator.wait_until_complete()
        pending = _pending_tui_work(tui)
        quiet_passes = 0 if pending else quiet_passes + 1
        if pending and loop.time() > deadline:
            raise AssertionError(f"TUI did not settle within {timeout}s; still pending: {pending}")
    await pilot.pause()


async def wait_until(predicate, what: str, timeout: float = 60.0) -> None:
    """Poll ``predicate`` with plain ``asyncio.sleep`` until it holds; fail naming ``what`` after ``timeout``.

    For screens with a ticking auto-refresh timer, where ``settle`` / ``pilot.pause()`` may never see an idle app.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for: {what}")
        await asyncio.sleep(0.05)


# ── Static surface ──────────────────────────────────────────────────────────


def _tui_modules() -> list[Any]:
    names = [tui_pkg.__name__]
    names += [m.name for m in pkgutil.walk_packages(tui_pkg.__path__, prefix=f"{tui_pkg.__name__}.")]
    return [importlib.import_module(n) for n in sorted(names)]


def _tui_classes() -> list[type]:
    found = []
    for module in _tui_modules():
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ == module.__name__ and issubclass(obj, DOMNode):
                found.append(obj)
    return sorted(found, key=lambda c: f"{c.__module__}:{c.__qualname__}")


def _binding(raw: Binding | tuple) -> dict:
    b = raw if isinstance(raw, Binding) else Binding(*raw)
    return {"key": b.key, "action": b.action, "description": b.description, "show": b.show, "priority": b.priority}


def _simple(value: Any) -> Any:
    """JSON-able view of a class/module constant (``None`` when it is not a plain value)."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, re.Pattern):
        return {"regex": value.pattern}
    if isinstance(value, type):
        return f"<class {value.__module__}.{value.__qualname__}>"
    if isinstance(value, list | tuple):
        return [_simple(v) for v in value]
    if isinstance(value, dict):
        return {str(_simple(k)): _simple(v) for k, v in value.items()}
    return f"<{type(value).__name__}>"


_HANDLER = re.compile(r"^(action_|on_|_on_|watch_)")


def _class_surface(cls: type) -> dict:
    own = cls.__dict__
    entry: dict[str, Any] = {"bases": [f"{b.__module__}.{b.__qualname__}" for b in cls.__bases__]}
    if "BINDINGS" in own:
        entry["BINDINGS"] = [_binding(b) for b in own["BINDINGS"]]
    if "DEFAULT_CSS" in own:
        entry["DEFAULT_CSS_sha256"] = hashlib.sha256(own["DEFAULT_CSS"].encode()).hexdigest()
    for name in sorted(own):
        if name.isupper() and name not in {"BINDINGS", "DEFAULT_CSS"}:
            entry[name] = _simple(own[name])
    entry["handlers"] = sorted(n for n in own if _HANDLER.match(n) and callable(own[n]))
    decorated = []
    for value in own.values():
        for message_type, selectors in getattr(value, "_textual_on", []):
            decorated.append(
                {
                    "message": message_type.__qualname__,
                    "selectors": {k: [s.css for s in v] for k, v in selectors.items()},
                }
            )
    if decorated:
        entry["on_decorated"] = sorted(decorated, key=lambda d: (d["message"], str(d["selectors"])))
    if "can_focus" in own:
        entry["can_focus"] = own["can_focus"]
    return entry


def _module_constants(module: Any) -> dict:
    """Public upper-case names *assigned* at the module's top level (re-imports are not pinned)."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        targets = (
            node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        names |= {t.id for t in targets if isinstance(t, ast.Name)}
    return {n: _simple(getattr(module, n)) for n in sorted(names) if n.isupper() and not n.startswith("_")}


def _resource_fields() -> dict:
    sample_plants = [{"id": 1, "species": "Monstera deliciosa"}, {"id": 2, "species": "Citrus limon"}]
    calls = {
        "cluster_fields()": resources.cluster_fields(),
        "plant_fields()": resources.plant_fields(),
        "sensor_fields(None, plants)": resources.sensor_fields(None, sample_plants),
        "sensor_fields(sensor, plants)": resources.sensor_fields({"name": "p", "type": "t", "plant_id": 2}, []),
        "irrigator_fields()": resources.irrigator_fields(),
        "irrigator_fields(irrigator)": resources.irrigator_fields({"name": "pump", "config": {"version": "3.5"}}),
        "config_fields()": resources.config_fields(),
        "window_fields()": resources.window_fields(),
        "vacation_fields()": resources.vacation_fields(),
        "vacation_fields(window)": resources.vacation_fields({"starts_at": 1, "ends_at": 2}),
        "preference_fields({})": resources.preference_fields({}),
    }
    return {name: [asdict(f) for f in fields] for name, fields in calls.items()}


def test_tui_static_surface_golden():
    """BINDINGS / modes / CSS_PATH / handler names / constants / form specs == golden."""
    surface = {
        "classes": {f"{c.__module__}:{c.__qualname__}": _class_surface(c) for c in _tui_classes()},
        "module_constants": {m.__name__: _module_constants(m) for m in _tui_modules() if _module_constants(m)},
        "resource_fields": _resource_fields(),
    }
    assert_golden_json("tui/surface.json", surface)


def test_every_screen_and_widget_class_is_in_the_surface():
    """The discovery walk sees the app, all screens/modals and all widgets — guards against a silent empty golden."""
    names = {c.__qualname__ for c in _tui_classes()}
    expected = {
        "GreenhouseApp",
        "DataScreen",
        "DashboardScreen",
        "ClusterScreen",
        "AlertsScreen",
        "ActivityScreen",
        "SystemScreen",
        "SettingsScreen",
        "SearchScreen",
        "ConfirmScreen",
        "IrrigateScreen",
        "WaterNowScreen",
        "LoginScreen",
        "FormScreen",
        "ClusterCard",
        "SpriteView",
        "Banner",
        "PlantTile",
        "MetricChart",
        "Heatmap",
        "KeyValue",
    }
    assert expected <= names, expected - names


def test_app_css_path_and_modes():
    """CSS_PATH resolves next to app.py; the five top-level modes map to their screens; dashboard is default."""
    assert GreenhouseApp.CSS_PATH == "app.tcss"
    assert GreenhouseApp.DEFAULT_MODE == "dashboard"
    assert {k: v.__name__ for k, v in GreenhouseApp.MODES.items()} == {
        "dashboard": "DashboardScreen",
        "alerts": "AlertsScreen",
        "activity": "ActivityScreen",
        "system": "SystemScreen",
        "settings": "SettingsScreen",
    }


# ── Sprites ─────────────────────────────────────────────────────────────────

SPRITE_CATEGORIES = ["tropical", "fern", "succulent", "cacti", "fruit_tree", "generic"]
# Six frames cover every animation cycle position: sway (4), sparkles (4) and the falling leaf (6).
SPRITE_FRAMES = range(6)
# Watering drops cycle every 3 frames.
WATERING_FRAMES = range(3)


def _sprite(sprite: sprites.PixelSprite) -> dict:
    return {"rows": list(sprite.rows), "palette": dict(sorted(sprite.palette.items()))}


def test_sprite_mood_frame_mapping_golden():
    """Every (category, mood, frame) sprite, watering overlays, logo, watering can and mood thresholds == golden."""
    moods = list(sprites.Mood)
    plants: dict[str, Any] = {}
    for category in SPRITE_CATEGORIES:
        plants[category] = {}
        for mood in moods:
            frames = [sprites.plant_sprite(category, mood, f) for f in SPRITE_FRAMES]
            plants[category][mood.value] = {
                "palette": dict(sorted(frames[0].palette.items())),
                "frames": [list(s.rows) for s in frames],
                "watering_frames": [
                    list(sprites.plant_sprite(category, mood, f, watering=True).rows) for f in WATERING_FRAMES
                ],
            }
    grid = [None, 0.0, 20.0, 29.9, 30.0, 39.9, 40.0, 54.9, 55.0, 70.0, 75.0, 75.1, 100.0]
    golden = {
        "mood_labels": {m.value: sprites.MOOD_LABELS[m] for m in moods},
        "mood_colors": {m.value: sprites.MOOD_COLORS[m] for m in moods},
        "mood_for_default_band": {str(v): sprites.mood_for(v).value for v in grid},
        "mood_for_band_55_80": {str(v): sprites.mood_for(v, 55.0, 80.0).value for v in grid},
        "normalize_category": {
            str(c): sprites.normalize_category(c)
            for c in [None, "", "Cactus", "fruit-tree", "Fruit Tree", "tree", "fruit", "herb", "mystery", "FERN"]
        },
        "plants": plants,
        "logo": _sprite(sprites.logo_sprite()),
        "watering_can": {
            "still": _sprite(sprites.watering_can_sprite()),
            "pouring": [_sprite(sprites.watering_can_sprite(pouring=True, frame=f)) for f in range(2)],
        },
    }
    assert_golden_json("tui/sprites.json", golden)
