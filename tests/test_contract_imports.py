"""Characterization: public import surfaces, test-patched attribute paths, logger names.

- ``contracts/imports.json`` — for each frozen module, ``sorted(n for n in dir(mod) if
  not n.startswith("_"))`` and ``__all__`` (declared order) when present. Re-imported
  names (``time``, ``logging``, ``tinytuya`` …) are part of the surface: a refactor that
  drops one from a frozen module needs a shim or a justification.
- every dotted attribute path that the existing tests patch or import privately
  still resolves, to the same
  kind of object;
- ``contracts/loggers.json`` — ``{module: {attr: logger.name}}`` for every module under
  ``libs/*/greenhouse_*`` that holds a module-level ``logging.Logger``. Loggers are
  ``getLogger(__name__)``, so moving a module silently renames its logger.

**Compatibility (superset) semantics** (orchestrator policy): these two goldens are
checked as *lower bounds*, not for equality. Every golden module must still import,
every golden public name must still be in its ``dir()``, and every golden ``__all__``
entry must still be in the module's ``__all__``; every golden module that still exists
must still hold a logger with each golden name. NEW names / modules / loggers are
allowed — additions break no consumer, and rule 8 forbids re-generating goldens after
the fact. ``GOLDEN_UPDATE=1`` still (re)writes the full observed snapshot.

Determinism: ``dir()`` of a package also lists submodules that *anything* in the
process happened to import, so the surfaces are captured in a fresh interpreter, one
forked child per module, importing only that module (heavy third-party deps are
pre-imported in the parent for speed; they never add ``greenhouse_*`` attributes).
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
import types

import pytest

from golden import GOLDEN_DIR, UPDATE_ENV_VAR, assert_golden_json

CORE_MODULES = [
    f"greenhouse_core.{name}"
    for name in (
        "models",
        "schemas",
        "repository",
        "constants",
        "utils",
        "plant_db",
        "logic",
        "devices",
        "learning",
        "sync",
        "stats",
        "database",
        "auth",
    )
]
SERVER_MODULES = [f"greenhouse_server.{name}" for name in ("app", "config", "deps", "auth", "scheduler")]
FROZEN_MODULES = CORE_MODULES + SERVER_MODULES

_SURFACE_SCRIPT = r"""
import json, os, sys
import fastapi, pydantic, sqlalchemy, alembic, apscheduler, tinytuya  # warm, no greenhouse_* side effects

out = {}
for name in sys.argv[1:]:
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        import importlib
        mod = importlib.import_module(name)
        all_ = getattr(mod, "__all__", None)
        data = {
            "public": sorted(n for n in dir(mod) if not n.startswith("_")),
            "__all__": list(all_) if all_ is not None else None,
        }
        with os.fdopen(w, "w") as fh:
            json.dump(data, fh)
        os._exit(0)
    os.close(w)
    with os.fdopen(r) as fh:
        payload = fh.read()
    _, status = os.waitpid(pid, 0)
    if status != 0 or not payload:
        raise SystemExit(f"child failed for {name}: status={status}")
    out[name] = json.loads(payload)
print(json.dumps(out))
"""

_LOGGER_SCRIPT = r"""
import importlib, json, logging, pkgutil

out, modules = {}, []
for pkg_name in ("greenhouse_core", "greenhouse_server", "greenhouse_cli"):
    pkg = importlib.import_module(pkg_name)
    names = [pkg_name] + [
        m.name
        for m in pkgutil.walk_packages(pkg.__path__, prefix=pkg_name + ".")
        if not m.name.startswith("greenhouse_core.migrations")  # env.py runs Alembic on import
    ]
    for name in sorted(names):
        mod = importlib.import_module(name)
        found = {attr: value.name for attr, value in vars(mod).items() if isinstance(value, logging.Logger)}
        if found:
            out[name] = found
        modules.append(name)
print(json.dumps({"modules": sorted(modules), "loggers": out}))
"""


def _run(script: str, *args: str) -> dict:
    proc = subprocess.run([sys.executable, "-c", script, *args], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _load_golden_or_update(name: str, observed) -> object | None:
    """Return the stored golden, or write ``observed`` and return None under GOLDEN_UPDATE=1."""
    if os.environ.get(UPDATE_ENV_VAR) == "1":
        assert_golden_json(name, observed)
        return None
    path = GOLDEN_DIR / name
    assert path.exists(), f"golden file missing: {path} (create it with {UPDATE_ENV_VAR}=1)"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.skipif(not hasattr(os, "fork"), reason="surface capture forks one child per module")
def test_public_import_surfaces_are_a_superset_of_golden(clean_env):
    observed = _run(_SURFACE_SCRIPT, *FROZEN_MODULES)
    golden = _load_golden_or_update("contracts/imports.json", observed)
    if golden is None:
        return
    problems = []
    for module, expected in golden.items():
        got = observed.get(module)
        if got is None:
            problems.append(f"{module}: module no longer captured")
            continue
        missing = sorted(set(expected["public"]) - set(got["public"]))
        if missing:
            problems.append(f"{module}: public names removed: {missing}")
        if expected["__all__"] is not None:
            missing_all = sorted(set(expected["__all__"]) - set(got["__all__"] or []))
            if missing_all:
                problems.append(f"{module}: __all__ entries removed: {missing_all}")
    assert not problems, "import surface shrank (add re-export shims):\n  " + "\n  ".join(problems)


def test_module_level_logger_names_are_kept(clean_env):
    observed = _run(_LOGGER_SCRIPT)
    loggers = observed["loggers"]
    # tests/test_migrations.py reads this logger by name.
    assert loggers["greenhouse_server.services.irrigation"] == {"logger": "greenhouse_server.services.irrigation"}
    golden = _load_golden_or_update("contracts/loggers.json", loggers)
    if golden is None:
        return
    problems = []
    for module, expected in golden.items():
        if module not in observed["modules"]:
            continue  # module removed/moved: allowed by policy (its logger goes with it)
        missing = sorted(set(expected.values()) - set(loggers.get(module, {}).values()))
        if missing:
            problems.append(f"{module}: logger names no longer present: {missing}")
    assert not problems, "logger names changed:\n  " + "\n  ".join(problems)


# --- dotted paths the existing tests patch / import privately -----------------


def _resolve(dotted: str):
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:cut]))
        except ModuleNotFoundError:
            continue
        for attr in parts[cut:]:
            obj = getattr(obj, attr)
        return obj
    raise AssertionError(f"cannot import any prefix of {dotted}")


PATCHED_PATHS = {
    # mock.patch strings
    "greenhouse_core.devices.tinytuya": "module",
    "greenhouse_core.devices.tinytuya.Cloud": "class",
    "greenhouse_core.devices.gateway.tinytuya": "module",
    "greenhouse_core.devices.gateway.tinytuya.OutletDevice": "class",
    "greenhouse_core.logic.engine.season_for": "function",
    "greenhouse_core.learning.issues.seasonal_light_factor": "function",
    "greenhouse_core.learning.issues.effective_light_threshold": "function",
    "greenhouse_cli.tui.run": "function",
    # monkeypatch.setattr targets
    "greenhouse_server.services.irrigation._time": "module",
    "greenhouse_core.repository.time.time": "builtin",
    "greenhouse_core.logic.engine.time.time": "builtin",
    "greenhouse_server.scheduler.logger.warning": "method",
    "greenhouse_server.scheduler._resolve_check_cron_hours": "function",
    "greenhouse_server.scheduler._app": "attribute",
    "greenhouse_server.services.leak.LeakDetectionService.check_after_irrigation": "function",
    "greenhouse_server.services.irrigation.IrrigationService.check_cluster": "function",
    # private names imported across modules
    "greenhouse_server.auth._get_settings": "function",
    "greenhouse_server.auth._session_from_app": "function",
    "greenhouse_server.auth._RedirectAuthError": "class",
}


def _kind(obj) -> str:
    if isinstance(obj, types.ModuleType):
        return "module"
    if isinstance(obj, type):
        return "class"
    if isinstance(obj, types.FunctionType):
        return "function"
    if isinstance(obj, types.BuiltinFunctionType):
        return "builtin"
    if isinstance(obj, types.MethodType):
        return "method"
    return "attribute"


@pytest.mark.parametrize("dotted", list(PATCHED_PATHS))
def test_patched_attribute_path_resolves(dotted):
    assert _kind(_resolve(dotted)) == PATCHED_PATHS[dotted]


def test_patched_modules_are_the_real_modules():
    """The patch targets alias the real modules, so patching them reaches production code."""
    import tinytuya

    assert _resolve("greenhouse_core.devices.tinytuya") is tinytuya
    assert _resolve("greenhouse_core.devices.gateway.tinytuya") is tinytuya
    assert _resolve("greenhouse_server.services.irrigation._time") is time
    assert _resolve("greenhouse_core.repository.time") is time
    assert _resolve("greenhouse_core.logic.engine.time") is time


def test_reexports_point_at_their_definitions():
    from greenhouse_core import constants, utils
    from greenhouse_core.logic import timing
    from greenhouse_server import scheduler

    assert scheduler.HEALTH_POLL_IDLE_MINUTES == constants.HEALTH_POLL_IDLE_MINUTES
    assert _resolve("greenhouse_core.logic.engine.season_for") is timing.season_for
    assert _resolve("greenhouse_core.logic.engine.seasonal_light_factor") is utils.seasonal_light_factor
    assert _resolve("greenhouse_core.learning.issues.seasonal_light_factor") is utils.seasonal_light_factor
    assert _resolve("greenhouse_core.learning.issues.effective_light_threshold") is utils.effective_light_threshold
