"""Refactor guard tests — cheap, fail-fast checks for rules the refactor must not break.

Each guard turns a code-review rule into a test:

1. Importing the CLI entry point does not import Textual (the TUI stays lazily imported).
2. No production module writes ``from time import time`` (it would defeat ``time.time`` patch seams).
3. No new public UPPERCASE module constant appears in the TUI package (fails before the surface golden).
4. No TUI ``DOMNode`` subclass shadows a name of its Textual base class (and none defines ``key_*``).
5. Every ``_``-prefixed helper in a framework-boundary module is fully annotated.

The allow-lists below are a baseline captured once (git tag ``refactor-gate1``). They may only shrink
(remove an entry once its offender is gone); a stale entry never fails a test.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest
from textual.dom import DOMNode

import greenhouse_cli.tui as tui_pkg
from greenhouse_cli.client import _default_token_path, load_stored_token
from greenhouse_server.config import Settings

DEFAULT_PORT = 8000  # Settings' built-in port, i.e. the .env value (9001) was ignored

ROOT = Path(__file__).resolve().parents[1]
LIBS = ROOT / "libs"
TUI_DIR = LIBS / "greenhouse-cli" / "greenhouse_cli" / "tui"
SURFACE_GOLDEN = ROOT / "tests" / "golden" / "tui" / "surface.json"

BOUNDARY_GLOBS = (
    "greenhouse-server/greenhouse_server/routes/*.py",
    "greenhouse-server/greenhouse_server/web/routes/*.py",
    "greenhouse-cli/greenhouse_cli/commands/*.py",
)

# Guard 4 baseline: names each TUI DOMNode subclass defines that also exist on its nearest Textual base.
SHADOWING_ALLOWED: dict[str, frozenset[str]] = {
    "greenhouse_cli.tui.app:GreenhouseApp": frozenset({"BINDINGS", "CSS_PATH", "DEFAULT_MODE", "MODES", "TITLE"}),
    "greenhouse_cli.tui.screens.activity:ActivityScreen": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.screens.alerts:AlertsScreen": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.screens.base:DataScreen": frozenset(),
    "greenhouse_cli.tui.screens.cluster:ClusterScreen": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.screens.dashboard:DashboardScreen": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.screens.forms:FormScreen": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.screens.modals:ConfirmScreen": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.screens.modals:IrrigateScreen": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.screens.modals:LoginScreen": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.screens.modals:WaterNowScreen": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.screens.search:SearchScreen": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.screens.settings:SettingsScreen": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.screens.system:SystemScreen": frozenset({"BINDINGS", "compose"}),
    # fix(tui): overriding Textual's two idle-time column measurements IS the fix for the stale narrow
    # render (they leave the render caches keyed on an unchanged _update_count); there is no public hook.
    "greenhouse_cli.tui.table:DataTable": frozenset({"_update_column_widths", "_update_dimensions"}),
    "greenhouse_cli.tui.widgets:Banner": frozenset(),
    "greenhouse_cli.tui.widgets:ClusterCard": frozenset({"BINDINGS", "compose"}),
    "greenhouse_cli.tui.widgets:Heatmap": frozenset(),
    "greenhouse_cli.tui.widgets:KeyValue": frozenset(),
    "greenhouse_cli.tui.widgets:MetricChart": frozenset(),
    "greenhouse_cli.tui.widgets:PlantTile": frozenset({"compose"}),
    "greenhouse_cli.tui.widgets:SpriteView": frozenset({"DEFAULT_CSS"}),
}

# Guard 5 baseline: boundary-module helpers that were not fully annotated when the baseline was captured.
UNANNOTATED_ALLOWED: frozenset[str] = frozenset(
    {
        "greenhouse-server/greenhouse_server/routes/scheduler.py::_set_paused",
        "greenhouse-server/greenhouse_server/web/routes/activity.py::_fetch_events",
        "greenhouse-server/greenhouse_server/web/routes/analytics.py::_set_check_all_paused_web",
        "greenhouse-server/greenhouse_server/web/routes/clusters.py::_plants_by_id",
        "greenhouse-server/greenhouse_server/web/routes/irrigators.py::_action_result",
        "greenhouse-server/greenhouse_server/web/routes/vacation.py::_next_window",
    }
)


def _production_files() -> list[Path]:
    files = sorted(LIBS.glob("*/greenhouse_*/**/*.py"))
    assert files, "no production modules found"
    return files


def _tui_module_names() -> list[str]:
    walked = pkgutil.walk_packages(tui_pkg.__path__, prefix=f"{tui_pkg.__name__}.")
    return [tui_pkg.__name__, *sorted(m.name for m in walked)]


# ── Guard 1 ──────────────────────────────────────────────────────────────────


def test_cli_entry_point_import_does_not_load_textual() -> None:
    """Run in a subprocess: in-process, other tests have already imported Textual."""
    code = (
        "import sys, greenhouse_cli.main\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] == 'textual')\n"
        "print(','.join(loaded))\n"
    )
    proc = subprocess.run(  # noqa: S603 — fixed argv, the running interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=ROOT, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", f"textual modules loaded by `import greenhouse_cli.main`: {proc.stdout}"


# ── Guard 2 ──────────────────────────────────────────────────────────────────


def test_no_from_time_import_time_in_production_code() -> None:
    """Production code reads the clock as ``time.time()`` so tests can freeze it (Conventions: Clock)."""
    offenders = [
        f"{path.relative_to(ROOT)}:{node.lineno}"
        for path in _production_files()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
        and node.module == "time"
        and any(alias.name == "time" for alias in node.names)
    ]
    assert offenders == [], f"`from time import time` defeats the time.time patch seams: {offenders}"


# ── Guard 3 ──────────────────────────────────────────────────────────────────


def _module_level_public_upper_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name) and sub.id.isupper() and not sub.id.startswith("_"):
                    names.add(sub.id)
    return names


def _module_name(path: Path) -> str:
    rel = path.relative_to(LIBS / "greenhouse-cli").with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def test_no_new_public_uppercase_constants_in_tui() -> None:
    """The TUI grows no public upper-case module constants beyond the frozen surface golden."""
    golden = json.loads(SURFACE_GOLDEN.read_text(encoding="utf-8"))["module_constants"]
    extra = {}
    for path in sorted(TUI_DIR.rglob("*.py")):
        module = _module_name(path)
        new = _module_level_public_upper_names(path) - set(golden.get(module, {}))
        if new:
            extra[module] = sorted(new)
    assert extra == {}, f"new public UPPERCASE TUI module constants (frozen surface): {extra}"


# ── Guard 4 ──────────────────────────────────────────────────────────────────


def _textual_base(cls: type) -> type:
    return next(base for base in cls.__mro__[1:] if base.__module__.split(".")[0] == "textual")


def _shadowed_names(cls: type) -> set[str]:
    base = _textual_base(cls)
    generated = set(vars(type("_GuardProbe", (base,), {})))  # attributes Textual sets on every subclass
    own = {name for name in vars(cls) if not (name.startswith("__") and name.endswith("__"))} - generated
    return own & set(dir(base))


def _tui_domnode_classes() -> dict[str, type]:
    found: dict[str, type] = {}
    for module_name in _tui_module_names():
        module = importlib.import_module(module_name)
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and obj.__module__ == module_name and issubclass(obj, DOMNode):
                found[f"{module_name}:{name}"] = obj
    return found


def test_tui_classes_do_not_shadow_textual_base_names() -> None:
    """Only the allow-listed TUI classes may shadow names of their Textual base."""
    classes = _tui_domnode_classes()
    assert classes, "no TUI DOMNode subclasses found"
    unknown = sorted(set(classes) - set(SHADOWING_ALLOWED))
    assert unknown == [], f"new TUI DOMNode subclasses (target §8 forbids them): {unknown}"
    shadowing = {
        key: sorted(_shadowed_names(cls) - SHADOWING_ALLOWED[key])
        for key, cls in classes.items()
        if _shadowed_names(cls) - SHADOWING_ALLOWED[key]
    }
    assert shadowing == {}, f"TUI classes shadow names of their Textual base: {shadowing}"


def test_tui_classes_define_no_key_handlers() -> None:
    """TUI classes use bindings, never ``key_*`` handlers."""
    offenders = {
        key: sorted(name for name in vars(cls) if name.startswith("key_"))
        for key, cls in _tui_domnode_classes().items()
        if any(name.startswith("key_") for name in vars(cls))
    }
    assert offenders == {}, f"`key_*` methods are Textual key handlers: {offenders}"


# ── Guard 5 ──────────────────────────────────────────────────────────────────


def _is_fully_annotated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = node.args
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs, *(a for a in (args.vararg, args.kwarg) if a)]
    params = [p for p in params if p.arg not in ("self", "cls")]
    return node.returns is not None and all(p.annotation is not None for p in params)


def _boundary_files() -> list[Path]:
    files = sorted(path for pattern in BOUNDARY_GLOBS for path in LIBS.glob(pattern))
    assert files, "no boundary-profile modules found"
    return files


@pytest.mark.parametrize("path", _boundary_files(), ids=lambda p: str(p.relative_to(LIBS)))
def test_boundary_module_helpers_are_fully_annotated(path: Path) -> None:
    """Private helpers in boundary modules are fully annotated."""
    rel = path.relative_to(LIBS).as_posix()
    missing = sorted(
        f"{rel}::{node.name}"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("_")
        and not _is_fully_annotated(node)
    )
    assert sorted(set(missing) - UNANNOTATED_ALLOWED) == [], "boundary helpers must be fully annotated (§10.13)"


# ── Guard: server fixtures survive mixed-directory command lines ─────────────


def test_server_fixtures_resolve_when_directories_are_mixed_on_one_command_line() -> None:
    """``tests/server/X tests/<core> tests/server/Y`` must not lose ``client`` for Y.

    pytest binds a directory conftest's fixtures to the first ``Package`` node it
    collects; a second ``server`` package (after a core file) could not see them.
    The server fixtures are therefore a global plugin (``server.fixtures``).
    """
    args = ["tests/server/test_well_known.py", "tests/test_utils.py", "tests/server/test_web_health.py"]
    result = subprocess.run(  # noqa: S603 — fixed argv, the running interpreter
        [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", "-p", "no:cacheprovider", "-n", "0", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "fixture 'client' not found" not in result.stdout


# ── Guard: tests never touch the developer's CLI token or .env ───────────────


def test_cli_token_resolution_is_isolated_from_the_developer(tmp_path_factory) -> None:
    """The autouse fixture points the CLI token lookup at a per-test temp dir and drops the env token."""
    assert "GREENHOUSE_API_TOKEN" not in os.environ
    assert _default_token_path().is_relative_to(tmp_path_factory.getbasetemp())
    assert load_stored_token() is None


def test_settings_ignore_a_developer_dotenv(tmp_path, monkeypatch) -> None:
    """Outside tests marked ``dotenv``, a ``.env`` in the working directory is not read by ``Settings``."""
    (tmp_path / ".env").write_text("IRRIGATION_PORT=9001\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IRRIGATION_PORT", raising=False)

    assert Settings().port == DEFAULT_PORT
