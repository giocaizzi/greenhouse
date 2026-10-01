"""Characterization: console scripts, the server entry point and package data (G7).

- ``[project.scripts]`` of the three distributions (read from each ``pyproject.toml``
  and from the installed metadata) — ``greenhouse`` and ``greenhouse-server``;
- ``greenhouse_server.app.main`` wiring: ``load_dotenv()`` → ``Settings()`` →
  ``create_app(settings)`` → ``uvicorn.run(app, host=settings.host, port=settings.port)``,
  with ``uvicorn.run`` / ``create_app`` / ``load_dotenv`` monkeypatched;
- the non-Python files each package ships (hatch includes every non-ignored file
  under the package dir) plus the Alembic migration scripts, which are loaded by
  path rather than imported — golden ``contracts/package_data.json`` — and the code
  paths that resolve them at runtime.

The full wheel listing is not a pytest test (building is slow); see
``refactor/scripts/check_wheels.sh`` and ``refactor/baseline/wheel-contents.txt``.
"""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import tomllib
from pathlib import Path

from sqlalchemy import create_engine

from golden import assert_golden_json

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "greenhouse-core": ("libs/greenhouse-core", "greenhouse_core"),
    "greenhouse-server": ("libs/greenhouse-server", "greenhouse_server"),
    "greenhouse-cli": ("libs/greenhouse-cli", "greenhouse_cli"),
}


def _pyproject(rel: str) -> dict:
    return tomllib.loads((REPO_ROOT / rel / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_console_scripts_and_wheel_packages():
    observed = {}
    for dist, (rel, _pkg) in PACKAGES.items():
        data = _pyproject(rel)
        observed[dist] = {
            "name": data["project"]["name"],
            "scripts": data["project"].get("scripts", {}),
            "wheel_packages": data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"],
            "build_backend": data["build-system"]["build-backend"],
        }
    assert observed == {
        "greenhouse-core": {
            "name": "greenhouse-core",
            "scripts": {},
            "wheel_packages": ["greenhouse_core"],
            "build_backend": "hatchling.build",
        },
        "greenhouse-server": {
            "name": "greenhouse-server",
            "scripts": {"greenhouse-server": "greenhouse_server.app:main"},
            "wheel_packages": ["greenhouse_server"],
            "build_backend": "hatchling.build",
        },
        "greenhouse-cli": {
            "name": "greenhouse-cli",
            "scripts": {"greenhouse": "greenhouse_cli.main:app"},
            "wheel_packages": ["greenhouse_cli"],
            "build_backend": "hatchling.build",
        },
    }
    root = _pyproject(".")
    assert "scripts" not in root["project"]
    assert root["project"]["dependencies"] == ["greenhouse-core", "greenhouse-server", "greenhouse-cli"]


def test_installed_console_scripts_resolve():
    eps = {
        ep.name: ep
        for ep in importlib.metadata.entry_points(group="console_scripts")
        if ep.dist is not None and ep.dist.name.startswith("greenhouse")
    }
    assert {name: (ep.value, ep.dist.name) for name, ep in eps.items()} == {
        "greenhouse": ("greenhouse_cli.main:app", "greenhouse-cli"),
        "greenhouse-server": ("greenhouse_server.app:main", "greenhouse-server"),
    }
    import typer

    from greenhouse_cli.main import app as cli_app
    from greenhouse_server.app import main as server_main

    assert eps["greenhouse"].load() is cli_app
    assert isinstance(cli_app, typer.Typer)
    assert eps["greenhouse-server"].load() is server_main


def test_server_main_wiring(clean_env, monkeypatch):
    """``main()`` loads ``.env`` first, then builds Settings, the app, and runs uvicorn."""
    import dotenv
    import uvicorn

    import greenhouse_server.app as app_mod

    calls: list[tuple] = []
    sentinel_app = object()

    def fake_load_dotenv(*args, **kwargs):
        calls.append(("load_dotenv", args, kwargs))
        # Settings() must be built *after* load_dotenv populated the environment.
        monkeypatch.setenv("IRRIGATION_PORT", "8765")
        monkeypatch.setenv("IRRIGATION_HOST", "127.0.0.9")

    def fake_create_app(*args, **kwargs):
        calls.append(("create_app", args, kwargs))
        return sentinel_app

    def fake_run(*args, **kwargs):
        calls.append(("uvicorn.run", args, kwargs))

    monkeypatch.setattr(dotenv, "load_dotenv", fake_load_dotenv)
    monkeypatch.setattr(app_mod, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", fake_run)

    assert app_mod.main() is None

    assert [c[0] for c in calls] == ["load_dotenv", "create_app", "uvicorn.run"]
    assert calls[0][1:] == ((), {})
    (settings,), create_kwargs = calls[1][1], calls[1][2]
    assert create_kwargs == {}
    assert isinstance(settings, app_mod.Settings)
    assert (settings.host, settings.port) == ("127.0.0.9", 8765)
    assert calls[2][1:] == ((sentinel_app,), {"host": "127.0.0.9", "port": 8765})


def _package_files(dist: str) -> list[str]:
    rel, pkg = PACKAGES[dist]
    root = REPO_ROOT / rel / pkg
    files = []
    for path in root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relpath = path.relative_to(root).as_posix()
        if path.suffix == ".py" and not relpath.startswith("migrations/"):
            continue
        files.append(relpath)
    return sorted(files)


def test_package_data_matches_golden():
    """Non-Python files (and Alembic scripts) per package, relative to the package root."""
    assert_golden_json("contracts/package_data.json", {dist: _package_files(dist) for dist in PACKAGES})


def test_runtime_resolution_of_package_data():
    """The code paths that locate package data still point at existing files."""
    from greenhouse_cli.tui.app import GreenhouseApp
    from greenhouse_core import database, plant_db
    from greenhouse_core.devices import profile
    from greenhouse_server.web import templating

    bundled = importlib.resources.files("greenhouse_core") / "data" / "plant_database.json"
    assert bundled.is_file()
    assert plant_db._DEFAULT_PLANT_DB_PATH == Path(str(bundled))

    core_root = Path(database.__file__).resolve().parent
    cfg = database._alembic_config(create_engine("sqlite://"))
    assert Path(cfg.config_file_name) == core_root / "alembic.ini"
    assert cfg.get_main_option("script_location") == str(core_root / "migrations")
    assert (core_root / "migrations" / "env.py").is_file()
    assert (core_root / "migrations" / "script.py.mako").is_file()

    assert profile._PROFILES_DIR == core_root / "devices" / "profiles"
    for name in ("ik10pw.json", "tr301z.json"):
        assert isinstance(profile.load_profile_json(name), dict)

    assert templating.TEMPLATES_DIR.is_dir()
    assert (templating.TEMPLATES_DIR / "_base.html").is_file()
    static = templating.TEMPLATES_DIR.parent / "static"
    assert (static / "app.css").is_file() and (static / "app.js").is_file()

    assert GreenhouseApp.CSS_PATH == "app.tcss"
    import greenhouse_cli.tui.app as tui_app_mod

    assert (Path(tui_app_mod.__file__).parent / GreenhouseApp.CSS_PATH).is_file()
