"""Characterization: the ``greenhouse`` CLI command tree, every ``--help`` page and every parameter.

Gap-list item G5 (``refactor/00-tests.md`` §6). The CLI interface and its ``--help``
text are a frozen public contract (``refactor/BRIEF.md`` rule 6), so this module pins:

- the full command tree in registration order (order is what ``--help`` lists);
- the exact ``--help`` output of every group and leaf command, one golden per page
  under ``tests/golden/cli/help/<dotted.command.path>.txt``;
- a JSON golden of every parameter (``tests/golden/cli/params.json``).

Rendering is made width/colour-stable independent of the developer's terminal:
``clean_env`` pins ``COLUMNS=100``/``TERM=dumb``/``NO_COLOR=1``; this module additionally
drops every variable rich/typer consult (``TERMINAL_WIDTH``, ``FORCE_COLOR``, ``LINES``, …)
and resets the import-time constants ``typer.rich_utils.MAX_WIDTH``/``FORCE_TERMINAL``,
which typer reads from the environment once at import.
"""

from __future__ import annotations

import re
from importlib.metadata import version
from typing import Any

import pytest
import rich
import typer.main
import typer.rich_utils
from typer.testing import CliRunner

from golden import assert_golden, assert_golden_json
from greenhouse_cli.main import app

PROG = "greenhouse"
HELP_GOLDEN_DIR = "cli/help"
EXPECTED_HELP_PAGES = 74  # 13 groups + 61 leaf commands (refactor/00-contracts.md §4.1)

# Environment variables rich / typer / click read that would change rendered output.
RENDER_ENV_VARS = (
    "TERMINAL_WIDTH",
    "_TYPER_FORCE_DISABLE_TERMINAL",
    "_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION",
    "TYPER_USE_RICH",
    "FORCE_COLOR",
    "PY_COLORS",
    "CLICOLOR",
    "CLICOLOR_FORCE",
    "GITHUB_ACTIONS",
    "TTY_COMPATIBLE",
    "TTY_INTERACTIVE",
    "JUPYTER_COLUMNS",
    "JUPYTER_LINES",
    "LINES",
)

_VERSION_RE = re.compile(re.escape(version("greenhouse-cli")))


def normalize_version(text: str) -> str:
    """Replace the release-please-managed package version with ``<VERSION>``."""
    return _VERSION_RE.sub("<VERSION>", text)


def help_golden_text(text: str) -> str:
    """Golden form of a help page: version normalized, trailing spaces stripped per line.

    Rich pads every line to the console width with spaces. The repo's pre-commit
    ``trailing-whitespace`` / ``end-of-file-fixer`` hooks would rewrite such a golden on
    commit, so the padding (pure layout, implied by the pinned width) is dropped. Interior
    spacing, line breaks and box drawing are kept byte-for-byte.
    """
    lines = [line.rstrip(" ") for line in normalize_version(text).split("\n")]
    return "\n".join(lines).rstrip("\n") + "\n"


@pytest.fixture
def stable_cli_env(clean_env, monkeypatch):
    """``clean_env`` plus a render-stable rich/typer setup (width 100, no colour, no terminal)."""
    for name in RENDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(typer.rich_utils, "MAX_WIDTH", None)  # width comes from COLUMNS=100
    monkeypatch.setattr(typer.rich_utils, "FORCE_TERMINAL", False)
    monkeypatch.setattr(rich, "_console", None)  # rich's global console caches its width at creation
    return clean_env


def walk_commands() -> list[tuple[tuple[str, ...], Any]]:
    """Return ``(path, command)`` for every group and command, depth-first in registration order."""
    root = typer.main.get_command(app)
    found: list[tuple[tuple[str, ...], Any]] = []

    def visit(path: tuple[str, ...], cmd: Any) -> None:
        found.append((path, cmd))
        if is_group(cmd):
            for name in cmd.list_commands(None):
                visit((*path, name), cmd.get_command(None, name))

    visit((), root)
    return found


def is_group(cmd: Any) -> bool:
    return hasattr(cmd, "commands")


def leaf_command_paths() -> list[tuple[str, ...]]:
    """Every invocable leaf command path (used by the JSON-output contract to demand a case per command)."""
    return [path for path, cmd in walk_commands() if not is_group(cmd)]


def golden_name(path: tuple[str, ...]) -> str:
    return ".".join((PROG, *path)) + ".txt"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return f"<{type(value).__name__}:{value!r}>"


def describe_param(param: Any) -> dict[str, Any]:
    ptype = param.type
    return {
        "name": param.name,
        "kind": param.param_type_name,
        "opts": list(param.opts),
        "secondary_opts": list(param.secondary_opts),
        "default": _jsonable(param.default),
        "required": param.required,
        "type": getattr(ptype, "name", type(ptype).__name__),
        "type_class": type(ptype).__name__,
        "min": _jsonable(getattr(ptype, "min", None)),
        "max": _jsonable(getattr(ptype, "max", None)),
        "envvar": _jsonable(param.envvar),
        "is_flag": getattr(param, "is_flag", False),
        "multiple": param.multiple,
        "nargs": param.nargs,
        "hidden": getattr(param, "hidden", False),
        "prompt": _jsonable(getattr(param, "prompt", None)),
        "hide_input": getattr(param, "hide_input", False),
        "is_eager": param.is_eager,
        "expose_value": param.expose_value,
        "help": getattr(param, "help", None),
    }


def describe_command(path: tuple[str, ...], cmd: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": " ".join((PROG, *path)),
        "group": is_group(cmd),
        "help": cmd.help,
        "short_help": cmd.short_help,
        "hidden": cmd.hidden,
        "deprecated": cmd.deprecated,
        "no_args_is_help": cmd.no_args_is_help,
        "params": [describe_param(p) for p in cmd.params],  # declaration order is the contract
    }
    if is_group(cmd):
        entry["invoke_without_command"] = cmd.invoke_without_command
        entry["subcommands"] = cmd.list_commands(None)  # registration order == --help listing order
    return entry


COMMANDS = walk_commands()
runner = CliRunner()


def test_command_tree_has_every_help_page():
    assert len(COMMANDS) == EXPECTED_HELP_PAGES
    groups = [p for p, c in COMMANDS if is_group(c)]
    assert len(groups) == 13
    assert len(leaf_command_paths()) == 61


def test_params_golden(stable_cli_env):
    """Every group/command with its params (name, opts, default, required, type, …) == golden."""
    # Re-walk under the fixture: the completion options depend on env read at get_command() time.
    tree = [describe_command(path, cmd) for path, cmd in walk_commands()]
    assert_golden_json("cli/params.json", tree)


@pytest.mark.parametrize("path", [p for p, _ in COMMANDS], ids=lambda p: ".".join((PROG, *p)))
def test_help_page_golden(stable_cli_env, path):
    """``greenhouse <path> --help`` → exit 0, nothing on stderr, stdout == golden."""
    result = runner.invoke(app, [*path, "--help"], prog_name=PROG)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout.endswith("╯\n\n")  # exact tail (one trailing blank line) — the golden drops it
    assert_golden(f"{HELP_GOLDEN_DIR}/{golden_name(path)}", help_golden_text(result.stdout))


@pytest.mark.parametrize("path", [p for p, c in COMMANDS if is_group(c)], ids=lambda p: ".".join((PROG, *p)) or PROG)
def test_group_without_args_prints_help_and_exits_2(stable_cli_env, path):
    """Pins current behavior: ``no_args_is_help`` groups print their help page (minus the final blank line), exit 2."""
    bare = runner.invoke(app, list(path), prog_name=PROG)
    helped = runner.invoke(app, [*path, "--help"], prog_name=PROG)
    assert bare.exit_code == 2
    assert bare.stderr == ""
    assert bare.stdout + "\n" == helped.stdout


def test_help_is_width_stable_against_hostile_terminal_env(stable_cli_env, monkeypatch):
    """A developer's ``TERMINAL_WIDTH``/``FORCE_COLOR`` must not leak into goldens once the fixture is applied.

    The fixture already deleted the variables; this sets hostile values *after* it and shows that only the
    import-time constants (which the fixture resets) could have carried them — rendering stays identical.
    """
    baseline = runner.invoke(app, ["config", "set", "--help"], prog_name=PROG).stdout
    monkeypatch.setenv("LINES", "7")
    monkeypatch.setenv("_TYPER_FORCE_DISABLE_TERMINAL", "1")
    monkeypatch.setattr(rich, "_console", None)
    again = runner.invoke(app, ["config", "set", "--help"], prog_name=PROG).stdout
    assert again == baseline
    assert "\x1b[" not in baseline  # no ANSI escapes
    assert max(len(line) for line in baseline.splitlines()) <= 100
