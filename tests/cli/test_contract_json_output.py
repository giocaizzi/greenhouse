"""Characterization: exact HTTP requests and exact stdout/stderr/exit code of the ``greenhouse`` CLI.

Gap-list item G6 (``refactor/00-tests.md`` §6). For every leaf command of the Typer tree
(derived from ``typer.main.get_command(app)`` — a newly added command without a case fails
``test_every_cli_command_has_a_case``) and for every public ``IrrigationClient`` method
(``test_every_client_method_has_a_case``), a canned ``httpx.MockTransport`` response is
served and the following are pinned in ``tests/golden/cli/requests_and_output.json``:

- every ``httpx.Client`` the CLI builds (``base_url``, ``timeout``, ``Authorization``);
- every request sent: method, full URL, path, query pairs in send order, raw body bytes,
  parsed JSON body, ``Authorization`` and ``Content-Type`` headers;
- the exact stdout / stderr text, the exit code, any non-``SystemExit`` exception type,
  and files the command writes (``stats --export``, the login token + its mode).

Also covered: server URL resolution (``--server`` → ``$IRRIGATION_SERVER_URL`` →
``http://localhost:8000``), token resolution (``$GREENHOUSE_API_TOKEN`` / token file under
an isolated ``$XDG_CONFIG_HOME`` / ``~/.config``), ``ServerError`` → exit 1 via
``commands/_helpers.py:call()``, ``check`` exit 2 with alerts, ``monitor`` exit 2 when
water is needed, usage errors exit 2, and the connection-refused message.

Normalization: the per-case temporary directory is replaced by ``<TMP>`` (it is random and
appears in ``login``'s "Token stored at …" message). Nothing else is normalized.

The golden is written by ``test_requests_and_output_golden`` (one writer, xdist-safe); the
parametrized ``test_cli_case`` / ``test_client_case`` re-run each case and compare it to its
entry so failures point at the offending command.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import rich
from typer.testing import CliRunner

from cli.test_contract_help import leaf_command_paths, stable_cli_env  # noqa: F401 — fixture re-export
from golden import GOLDEN_DIR, UPDATE_ENV_VAR, assert_golden_json, to_canonical_json
from greenhouse_cli.client import IrrigationClient, ServerError
from greenhouse_cli.main import app

# Every test runs under clean_env + render-stable rich/typer settings (fixture from test_contract_help).
pytestmark = pytest.mark.usefixtures("stable_cli_env")

GOLDEN = "cli/requests_and_output.json"
PROG = "greenhouse"

# Rich canned body (unicode, float, big int, bool, null, nested list/dict, non-sorted keys) used by the
# ``output.*`` / ``ok.*`` cases to pin rich.print_json formatting (indent, ensure_ascii, key order kept).
# Every other request is answered with a small echo body ``{"echo": "<METHOD> <path>", "ok": true}``.
DEFAULT_BODY: dict[str, Any] = {
    "id": 7,
    "name": "Serra ☘ café",
    "ratio": 0.5,
    "big": 12345678901,
    "ok": True,
    "missing": None,
    "tags": ["b", 2, 3.25],
    "nested": {"z": 1, "a": [{"k": "v"}]},
}


@dataclass(frozen=True)
class Raw:
    """A raw response: exact status, body text and content type."""

    status: int
    content: str
    content_type: str


@dataclass(frozen=True)
class Raise:
    """The transport raises ``httpx.<exc>(message)`` instead of responding."""

    exc: str
    message: str


@dataclass(frozen=True)
class Case:
    id: str
    argv: tuple[str, ...]
    routes: dict[tuple[str, str], Any] = field(default_factory=dict)
    env: dict[str, str | None] = field(default_factory=dict)
    input: str | None = None
    token_file: str | None = None  # pre-existing content of $XDG_CONFIG_HOME/greenhouse/token
    files: tuple[str, ...] = ()  # files (relative to cwd) whose content is pinned after the run
    xdg: bool = True  # False → $XDG_CONFIG_HOME unset, token lives under $HOME/.config


def C(case_id: str, *argv: str, **kw: Any) -> Case:  # noqa: N802 — table-builder shorthand
    return Case(case_id, argv, **kw)


P = "/api/v1"
CLUSTERS_TWO = [{"id": 1, "name": "Living Room"}, {"id": 2, "name": "Balcony"}]

CLI_CASES: list[Case] = [
    # ── operations ──
    C("status", "status", "3"),
    C("irrigate.defaults", "irrigate", "3"),
    C("irrigate.all_options", "irrigate", "3", "--temp", "31.5", "--dry-run", "--no-sync", "--force"),
    C(
        "irrigate.action_error_exits_1",
        "irrigate",
        "3",
        routes={("POST", f"{P}/clusters/3/irrigate"): (200, {"action": "error", "error": "pump offline"})},
    ),
    C("check.cluster", "check", "3"),
    C("check.all", "check", "--all"),
    C("check.cluster_and_all_prefers_all", "check", "3", "--all"),
    C("check.no_target_exits_1", "check"),
    C("check.cluster_zero_current_behavior", "check", "0"),
    C(
        "check.has_alerts_exits_2",
        "check",
        "--all",
        routes={("POST", f"{P}/check"): (200, {"results": [{"alerts": ["low"]}], "has_alerts": True})},
    ),
    C(
        "check.action_error_exits_1",
        "check",
        "3",
        routes={("POST", f"{P}/clusters/3/check"): (200, {"action": "error", "has_alerts": False})},
    ),
    C(
        "check.alerts_win_over_error_exits_2",
        "check",
        "3",
        routes={("POST", f"{P}/clusters/3/check"): (200, {"action": "error", "has_alerts": True})},
    ),
    C(
        "check.list_response_exits_0",
        "check",
        "--all",
        routes={("POST", f"{P}/check"): (200, [{"has_alerts": True}])},
    ),
    C(
        "monitor.dry_enough_exits_0",
        "monitor",
        "1",
        routes={("GET", f"{P}/clusters/1/monitor"): (200, {"cluster_name": "C1", "needs_water": []})},
    ),
    C(
        "monitor.needs_water_exits_2",
        "monitor",
        "1",
        routes={("GET", f"{P}/clusters/1/monitor"): (200, {"cluster_name": "C1", "needs_water": ["Sensor A: 30%"]})},
    ),
    C("sync.defaults", "sync"),
    C("sync.hours", "sync", "--hours", "6"),
    C("learn", "learn", "3"),
    C("history.defaults", "history", "3"),
    C("history.options", "history", "3", "--hours", "48", "--limit", "5"),
    C("stats.defaults", "stats", "3"),
    C("stats.days", "stats", "3", "--days", "30"),
    C(
        "stats.export_csv",
        "stats",
        "3",
        "--days",
        "14",
        "--export",
        "out.csv",
        routes={("GET", f"{P}/clusters/3/stats/export"): (200, "timestamp,minutes\n1,5\n")},
        files=("out.csv",),
    ),
    C("stats.export_json_response_writes_empty", "stats", "3", "--export", "out.csv", files=("out.csv",)),
    C("health", "health"),
    C("stop-all.yes", "stop-all", "--yes"),
    C("stop-all.short_y", "stop-all", "-y"),
    C("stop-all.prompt_declined", "stop-all", input="n\n"),
    C("stop-all.prompt_confirmed", "stop-all", input="y\n"),
    # ── auth ──
    C(
        "login.stores_token",
        "login",
        "--username",
        "admin",
        "--password",
        "s3cret",
        routes={("POST", f"{P}/auth/login"): (200, {"access_token": "jwt-abc", "username": "admin"})},
    ),
    C(
        "login.ignores_stored_and_env_token",
        "login",
        "--username",
        "admin",
        "--password",
        "s3cret",
        routes={("POST", f"{P}/auth/login"): (200, {"access_token": "jwt-new"})},
        env={"GREENHOUSE_API_TOKEN": "env-token"},
        token_file="old-token",
    ),
    C(
        "login.prompts",
        "login",
        input="admin\ns3cret\n",
        routes={("POST", f"{P}/auth/login"): (200, {"access_token": "jwt-abc", "username": "admin"})},
    ),
    C(
        "login.print_token",
        "login",
        "--username",
        "admin",
        "--password",
        "s3cret",
        "--print-token",
        routes={("POST", f"{P}/auth/login"): (200, {"access_token": "jwt-abc"})},
    ),
    C(
        "login.empty_token",
        "login",
        "--username",
        "admin",
        "--password",
        "x",
        routes={("POST", f"{P}/auth/login"): (200, {"access_token": ""})},
    ),
    C(
        "login.failure_exits_1",
        "login",
        "--username",
        "admin",
        "--password",
        "bad",
        routes={("POST", f"{P}/auth/login"): (401, {"detail": "Invalid credentials"})},
    ),
    C("logout.with_token", "logout", token_file="stored-jwt"),
    C("logout.without_token", "logout"),
    C(
        "logout.server_error_still_clears",
        "logout",
        token_file="stored-jwt",
        routes={("POST", f"{P}/auth/logout"): (500, {"detail": "boom"})},
    ),
    C(
        "logout.unreachable_still_clears",
        "logout",
        token_file="stored-jwt",
        routes={("POST", f"{P}/auth/logout"): Raise("ConnectError", "[Errno 111] Connection refused")},
    ),
    C("whoami", "whoami", token_file="stored-jwt"),
    # ── server URL resolution ──
    C("server.default_localhost", "health"),
    C("server.env", "health", env={"IRRIGATION_SERVER_URL": "http://env.example:9001"}),
    C("server.flag", "--server", "http://192.0.2.10:8000", "health"),
    C(
        "server.flag_wins_over_env",
        "--server",
        "http://flag.example:9002",
        "health",
        env={"IRRIGATION_SERVER_URL": "http://env.example:9001"},
    ),
    C(
        "server.empty_flag_falls_back_to_env",
        "--server",
        "",
        "health",
        env={"IRRIGATION_SERVER_URL": "http://env.example:9001"},
    ),
    C("server.base_path_prefix", "--server", "http://proxy.example/greenhouse", "health"),
    # ── token resolution ──
    C("token.none", "health"),
    C("token.file_stripped", "health", token_file="  file-token \n"),
    C("token.empty_file", "health", token_file="  \n"),
    C("token.env_wins_over_file", "health", env={"GREENHOUSE_API_TOKEN": "  env-token \n"}, token_file="file-token"),
    C(
        "token.blank_env_current_behavior_ignores_file",
        "health",
        env={"GREENHOUSE_API_TOKEN": "   "},
        token_file="file-token",
    ),
    C("token.home_config_without_xdg", "health", token_file="home-token", xdg=False),
    # ── error handling (ServerError → exit 1 via _helpers.call) ──
    C("error.404_detail", "status", "999", routes={("GET", f"{P}/clusters/999/status"): (404, {"detail": "Nope"})}),
    C(
        "error.500_plain_text",
        "health",
        routes={("GET", f"{P}/health"): Raw(500, "Internal Server Error", "text/plain")},
    ),
    C(
        "error.422_detail_list_python_repr",
        "health",
        routes={("GET", f"{P}/health"): (422, {"detail": [{"loc": ["body", "x"], "msg": "bad"}]})},
    ),
    C("error.json_without_detail", "health", routes={("GET", f"{P}/health"): (503, {"error": "down"})}),
    C("error.json_list_body", "health", routes={("GET", f"{P}/health"): (400, ["a", "b"])}),
    C(
        "error.connection_refused",
        "health",
        routes={("GET", f"{P}/health"): Raise("ConnectError", "[Errno 111] Connection refused")},
    ),
    C(
        "error.read_timeout_current_behavior_uncaught",
        "health",
        routes={("GET", f"{P}/health"): Raise("ReadTimeout", "timed out")},
    ),
    C(
        "error.empty_200_body_current_behavior_uncaught",
        "health",
        routes={("GET", f"{P}/health"): Raw(200, "", "application/json")},
    ),
    C("output.rich_json_formatting", "health", routes={("GET", f"{P}/health"): (200, DEFAULT_BODY)}),
    C("output.csv_content_type_on_json_command", "health", routes={("GET", f"{P}/health"): (200, "a,b\n1,2\n")}),
    # ── usage errors (exit 2) ──
    C("usage.unknown_command", "nope"),
    C("usage.unknown_subcommand", "cluster", "nope"),
    C("usage.missing_argument", "status"),
    C("usage.bad_int", "status", "abc"),
    C("usage.missing_required_option", "config", "get"),
    C("usage.alerts_limit_out_of_range", "alerts", "list", "--limit", "0"),
    C("usage.windows_hour_out_of_range", "windows", "add", "--cluster", "1", "--start-hour", "24", "--end-hour", "2"),
    C("tui.refresh_negative_usage_error", "tui", "--refresh", "-1"),
    # ── cluster ──
    C("cluster.add.defaults", "cluster", "add", "Living Room"),
    C("cluster.add.options", "cluster", "add", "Balcony", "--location", "South wall", "--environment", "outdoor"),
    C("cluster.list", "cluster", "list"),
    C("cluster.get", "cluster", "get", "4"),
    C("cluster.update.no_fields", "cluster", "update", "4"),
    C("cluster.update.some_fields", "cluster", "update", "4", "--name", "Renamed", "--environment", "outdoor"),
    C("cluster.delete.yes", "cluster", "delete", "4", "--yes"),
    C("cluster.delete.short_y", "cluster", "delete", "4", "-y"),
    C("cluster.delete.prompt_declined", "cluster", "delete", "4", input="n\n"),
    C("cluster.delete.prompt_confirmed", "cluster", "delete", "4", input="y\n"),
    # ── plant ──
    C("plant.add.minimal", "plant", "add", "Monstera deliciosa", "--cluster", "2"),
    C(
        "plant.add.all_options",
        "plant",
        "add",
        "Ficus",
        "--cluster",
        "2",
        "--category",
        "tropical",
        "--water-needs",
        "high",
        "--light-needs",
        "low",
        "--temp-min",
        "18",
        "--temp-max",
        "27.5",
        "--humidity-min",
        "60",
        "--humidity-max",
        "80",
        "--notes",
        "by the window",
    ),
    C("plant.list.cluster", "plant", "list", "--cluster", "3"),
    C(
        "plant.list.all_clusters",
        "plant",
        "list",
        routes={
            ("GET", f"{P}/clusters"): (200, CLUSTERS_TWO),
            ("GET", f"{P}/clusters/1/plants"): (200, [{"id": 10, "species": "Monstera"}]),
            ("GET", f"{P}/clusters/2/plants"): (200, []),
        },
    ),
    C(
        "plant.list.cluster_zero_current_behavior_lists_all",
        "plant",
        "list",
        "--cluster",
        "0",
        routes={("GET", f"{P}/clusters"): (200, [])},
    ),
    C("plant.sync.defaults", "plant", "sync"),
    C("plant.sync.options", "plant", "sync", "--plant-id", "5", "--cluster", "2"),
    C("plant.move", "plant", "move", "5", "--to-cluster", "2"),
    C("plant.update.no_fields", "plant", "update", "5", "--cluster", "2"),
    C(
        "plant.update.some_fields",
        "plant",
        "update",
        "5",
        "--cluster",
        "2",
        "--species",
        "Ficus",
        "--temp-min",
        "15",
        "--notes",
        "moved",
    ),
    C("plant.delete.yes", "plant", "delete", "5", "--cluster", "2", "--yes"),
    C("plant.delete.short_y", "plant", "delete", "5", "--cluster", "2", "-y"),
    C("plant.delete.prompt_declined", "plant", "delete", "5", "--cluster", "2", input="n\n"),
    C("plant.delete.prompt_confirmed", "plant", "delete", "5", "--cluster", "2", input="y\n"),
    # ── irrigator ──
    C(
        "irrigator.add.minimal",
        "irrigator",
        "add",
        "--cluster",
        "2",
        "--device-id",
        "fake_tuya_device_aabbccdd",
        "--name",
        "Pump",
        "--type",
        "tuya_cloud",
    ),
    C(
        "irrigator.add.all_options",
        "irrigator",
        "add",
        "--cluster",
        "2",
        "--device-id",
        "fake_tuya_device_aabbccdd",
        "--name",
        "Pump",
        "--type",
        "tuya_local",
        "--device-ip",
        "192.0.2.20",
        "--local-key",
        "fake_local_key_0000",
        "--reservoir-l",
        "12.5",
        "--flow-rate-l-per-min",
        "0.75",
    ),
    C(
        "irrigator.add.empty_ip_sent",
        "irrigator",
        "add",
        "--cluster",
        "2",
        "--device-id",
        "fake_tuya_device_aabbccdd",
        "--name",
        "Pump",
        "--type",
        "tuya_local",
        "--device-ip",
        "",
    ),
    C("irrigator.update.empty_key_sent", "irrigator", "update", "2", "--local-key", ""),
    C("irrigator.list", "irrigator", "list"),
    C("irrigator.show", "irrigator", "show", "2"),
    C("irrigator.start.defaults", "irrigator", "start", "9"),
    C("irrigator.start.minutes", "irrigator", "start", "9", "--minutes", "5"),
    C("irrigator.stop", "irrigator", "stop", "9"),
    C("irrigator.log-manual.minimal", "irrigator", "log-manual", "9", "--minutes", "4"),
    C("irrigator.log-manual.notes", "irrigator", "log-manual", "9", "--minutes", "4", "--notes", "hand watered"),
    C("irrigator.update.no_fields", "irrigator", "update", "2"),
    C("irrigator.update.device_ip_only", "irrigator", "update", "2", "--device-ip", "192.0.2.21"),
    C(
        "irrigator.update.all_options",
        "irrigator",
        "update",
        "2",
        "--name",
        "Pump 2",
        "--type",
        "tuya_local",
        "--device-ip",
        "192.0.2.21",
        "--local-key",
        "fake_local_key_1111",
        "--reservoir-l",
        "10",
        "--flow-rate-l-per-min",
        "1.5",
    ),
    C("irrigator.delete.yes", "irrigator", "delete", "2", "--yes"),
    C("irrigator.delete.short_y", "irrigator", "delete", "2", "-y"),
    C("irrigator.delete.prompt_declined", "irrigator", "delete", "2", input="n\n"),
    C("irrigator.delete.prompt_confirmed", "irrigator", "delete", "2", input="y\n"),
    # ── sensor ──
    C(
        "sensor.add.minimal",
        "sensor",
        "add",
        "--cluster",
        "2",
        "--device-id",
        "fake_sensor_001",
        "--name",
        "Soil",
        "--type",
        "soil_moisture",
    ),
    C(
        "sensor.add.plant",
        "sensor",
        "add",
        "--cluster",
        "2",
        "--device-id",
        "fake_sensor_001",
        "--name",
        "Soil",
        "--type",
        "soil_moisture",
        "--plant-id",
        "5",
    ),
    C("sensor.list.cluster", "sensor", "list", "--cluster", "3"),
    C(
        "sensor.list.all_clusters",
        "sensor",
        "list",
        routes={
            ("GET", f"{P}/clusters"): (200, CLUSTERS_TWO),
            ("GET", f"{P}/clusters/1/sensors"): (200, []),
            ("GET", f"{P}/clusters/2/sensors"): (200, [{"id": 20, "name": "Soil"}]),
        },
    ),
    C(
        "sensor.list.cluster_zero_current_behavior_lists_all",
        "sensor",
        "list",
        "--cluster",
        "0",
        routes={("GET", f"{P}/clusters"): (200, [])},
    ),
    C("sensor.update.no_fields", "sensor", "update", "20", "--cluster", "2"),
    C(
        "sensor.update.all_options",
        "sensor",
        "update",
        "20",
        "--cluster",
        "2",
        "--name",
        "Soil 2",
        "--type",
        "temp_humidity",
        "--plant-id",
        "6",
    ),
    C("sensor.delete.yes", "sensor", "delete", "20", "--cluster", "2", "--yes"),
    C("sensor.delete.short_y", "sensor", "delete", "20", "--cluster", "2", "-y"),
    C("sensor.delete.prompt_declined", "sensor", "delete", "20", "--cluster", "2", input="n\n"),
    C("sensor.delete.prompt_confirmed", "sensor", "delete", "20", "--cluster", "2", input="y\n"),
    # ── config ──
    C("config.set.minimal", "config", "set", "--cluster", "2"),
    C(
        "config.set.all_options",
        "config",
        "set",
        "--cluster",
        "2",
        "--mode",
        "smart",
        "--minutes",
        "3",
        "--interval",
        "12",
        "--no-auto-run",
        "--daily-cap",
        "20",
        "--max-events",
        "4",
        "--quiet-start",
        "22",
        "--quiet-end",
        "6",
    ),
    C("config.get", "config", "get", "--cluster", "2"),
    C("config.effective", "config", "effective", "--cluster", "2"),
    C("config.global.get", "config", "global", "get"),
    C("config.global.set.minimal", "config", "global", "set"),
    C(
        "config.global.set.all_options",
        "config",
        "global",
        "set",
        "--mode",
        "schedule",
        "--minutes",
        "2",
        "--interval",
        "24",
        "--auto-run",
        "--daily-cap",
        "10",
        "--max-events",
        "2",
        "--quiet-start",
        "0",
        "--quiet-end",
        "0",
    ),
    # ── scheduler ──
    C("scheduler.pause", "scheduler", "pause"),
    C("scheduler.resume", "scheduler", "resume"),
    C("scheduler.status", "scheduler", "status"),
    # ── alerts ──
    C("alerts.list.defaults", "alerts", "list"),
    C(
        "alerts.list.all_filters",
        "alerts",
        "list",
        "--status",
        "open",
        "--cluster",
        "2",
        "--plant",
        "5",
        "--limit",
        "500",
    ),
    C("alerts.get", "alerts", "get", "11"),
    C("alerts.ack", "alerts", "ack", "11"),
    C("alerts.resolve", "alerts", "resolve", "11"),
    C("alerts.sync.all", "alerts", "sync"),
    C("alerts.sync.cluster", "alerts", "sync", "--cluster", "2"),
    # ── decisions ──
    C("decisions.list.defaults", "decisions", "list", "--cluster", "2"),
    C("decisions.list.limit", "decisions", "list", "--cluster", "2", "--limit", "5"),
    # ── prefs ──
    C("prefs.get", "prefs", "get"),
    C("prefs.set.no_fields", "prefs", "set"),
    C(
        "prefs.set.all_options",
        "prefs",
        "set",
        "--units",
        "imperial",
        "--timezone",
        "Europe/Rome",
        "--theme",
        "dark",
        "--refresh-interval",
        "60",
        "--no-dry-run-global",
        "--default-cluster",
        "2",
    ),
    # ── vacation ──
    C("vacation.list", "vacation", "list"),
    C("vacation.add.minimal", "vacation", "add", "--starts-at", "1776247200", "--ends-at", "1776852000"),
    C(
        "vacation.add.all_options",
        "vacation",
        "add",
        "--starts-at",
        "1776247200",
        "--ends-at",
        "1776852000",
        "--email",
        "sitter@example.com",
        "--notes",
        "neighbour waters",
    ),
    C("vacation.update.no_fields", "vacation", "update", "8"),
    C("vacation.update.some_fields", "vacation", "update", "8", "--ends-at", "1776938400", "--notes", "extended"),
    C("vacation.delete.yes", "vacation", "delete", "8", "--yes"),
    C("vacation.delete.short_y", "vacation", "delete", "8", "-y"),
    C("vacation.delete.prompt_declined", "vacation", "delete", "8", input="n\n"),
    C("vacation.delete.prompt_confirmed", "vacation", "delete", "8", input="y\n"),
    # ── windows ──
    C("windows.list", "windows", "list", "--cluster", "2"),
    C("windows.add.minimal", "windows", "add", "--cluster", "2", "--start-hour", "6", "--end-hour", "9"),
    C(
        "windows.add.all_options",
        "windows",
        "add",
        "--cluster",
        "2",
        "--start-hour",
        "22",
        "--end-hour",
        "2",
        "--weekday-mask",
        "65",
        "--label",
        "night",
    ),
    C("windows.update.no_fields", "windows", "update", "3", "--cluster", "2"),
    C(
        "windows.update.all_options",
        "windows",
        "update",
        "3",
        "--cluster",
        "2",
        "--start-hour",
        "0",
        "--end-hour",
        "23",
        "--weekday-mask",
        "1",
        "--label",
        "",
    ),
    C("windows.delete.yes", "windows", "delete", "3", "--cluster", "2", "--yes"),
    C("windows.delete.short_y", "windows", "delete", "3", "--cluster", "2", "-y"),
    C("windows.delete.prompt_declined", "windows", "delete", "3", "--cluster", "2", input="n\n"),
    C("windows.delete.prompt_confirmed", "windows", "delete", "3", "--cluster", "2", input="y\n"),
]


@dataclass(frozen=True)
class ClientCase:
    id: str
    method: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    response: Any = None  # None → echo body


def M(case_id: str, method: str, *args: Any, response: Any = None, **kwargs: Any) -> ClientCase:  # noqa: N802
    return ClientCase(case_id, method, args, kwargs, response)


CLIENT_CASES: list[ClientCase] = [
    M("login", "login", "admin", "s3cret"),
    M("logout", "logout"),
    M("whoami", "whoami"),
    M("create_cluster.defaults", "create_cluster", "Living Room"),
    M("create_cluster.all", "create_cluster", "Balcony", location="South", environment="outdoor"),
    M("list_clusters", "list_clusters"),
    M("get_cluster", "get_cluster", 4),
    M("get_cluster_detail", "get_cluster_detail", 4),
    M("update_cluster.drops_none", "update_cluster", 4, name="N", location=None, environment="outdoor"),
    M("delete_cluster", "delete_cluster", 4),
    M("add_plant.keeps_none", "add_plant", 2, species="Ficus", category=None),
    M("list_plants", "list_plants", 2),
    M("update_plant.drops_none", "update_plant", 2, 5, species="Ficus", notes=None),
    M("delete_plant", "delete_plant", 2, 5),
    M("sync_plants.defaults", "sync_plants"),
    M("sync_plants.all", "sync_plants", plant_id=5, cluster_id=2),
    M("move_plant", "move_plant", 5, 3),
    M("list_irrigators", "list_irrigators"),
    M("add_irrigator.drops_none", "add_irrigator", 2, tuya_device_id="fake_tuya_device_aabbccdd", config=None),
    M("get_irrigator", "get_irrigator", 2),
    M("update_irrigator.drops_none", "update_irrigator", 2, name="P", config=None),
    M("delete_irrigator", "delete_irrigator", 2),
    M("start_irrigator.defaults", "start_irrigator", 9),
    M("start_irrigator.minutes", "start_irrigator", 9, 5),
    M("stop_irrigator", "stop_irrigator", 9),
    M("log_manual.defaults", "log_manual", 9, 4),
    M("log_manual.notes", "log_manual", 9, 4, "hand"),
    M("add_sensor.keeps_none", "add_sensor", 2, tuya_device_id="fake_sensor_001", plant_id=None),
    M("list_sensors", "list_sensors", 2),
    M("update_sensor.drops_none", "update_sensor", 2, 20, name="S", plant_id=None),
    M("delete_sensor", "delete_sensor", 2, 20),
    M("set_config.drops_none", "set_config", 2, mode="smart", auto_run=False, duration_minutes=None),
    M("get_config", "get_config", 2),
    M("get_effective_config", "get_effective_config", 2),
    M("get_global_config", "get_global_config"),
    M("update_global_config.drops_none", "update_global_config", quiet_start_hour=0, mode=None),
    M("status", "status", 3),
    M("irrigate.defaults", "irrigate", 3),
    M("irrigate.all", "irrigate", 3, temp_override=30.0, dry_run=True, no_sync=True, force=True),
    M("monitor", "monitor", 3),
    M("check.all", "check"),
    M("check.cluster", "check", 3),
    M("sync.defaults", "sync"),
    M("sync.hours", "sync", 6),
    M("bulk_stop_all", "bulk_stop_all"),
    M("learn", "learn", 3),
    M("history.defaults", "history", 3),
    M("history.all", "history", 3, hours=48, limit=5),
    M("stats.defaults", "stats", 3),
    M("stats.days", "stats", 3, days=30),
    M("stats_export.csv", "stats_export", 3, response=(200, "a,b\n1,2\n")),
    M("stats_export.json_response_returns_empty", "stats_export", 3, days=2),
    M("health", "health"),
    M("scheduler_jobs", "scheduler_jobs"),
    M("delete_scheduler_job.path_not_escaped", "delete_scheduler_job", "job/with space"),
    M("scheduler_pause", "scheduler_pause"),
    M("scheduler_resume", "scheduler_resume"),
    M("system_health", "system_health"),
    M("forecast", "forecast", 3),
    M("list_activity.defaults", "list_activity"),
    M("list_activity.all", "list_activity", limit=5, before=99, severity="warning", kind=None, cluster_id=2),
    M("insights", "insights", 3),
    M("efficacy.defaults", "efficacy", 3),
    M("efficacy.days", "efficacy", 3, days=30),
    M("quality_report", "quality_report"),
    M("search.defaults", "search", "mon stera&x"),
    M("search.limit", "search", "fern", limit=3),
    M("plant_health", "plant_health", 5),
    M("health_snapshot", "health_snapshot"),
    M("cluster_chart_data.defaults", "cluster_chart_data", 3),
    M("cluster_chart_data.all", "cluster_chart_data", 3, hours=72, metric="temperature"),
    M("plant_chart_data.defaults", "plant_chart_data", 5),
    M("plant_chart_data.all", "plant_chart_data", 5, hours=6, metric="humidity"),
    M("cluster_overlay.defaults", "cluster_overlay", 3),
    M("cluster_overlay.hours", "cluster_overlay", 3, hours=24),
    M("cluster_heatmap.defaults", "cluster_heatmap", 3),
    M("cluster_heatmap.days", "cluster_heatmap", 3, days=7),
    M("plant_health_timeline", "plant_health_timeline", 5),
    M("list_alerts.defaults", "list_alerts"),
    M("list_alerts.all", "list_alerts", status="open", cluster_id=2, plant_id=5, limit=3),
    M("get_alert", "get_alert", 11),
    M("acknowledge_alert", "acknowledge_alert", 11),
    M("resolve_alert", "resolve_alert", 11),
    M("sync_alerts.all", "sync_alerts"),
    M("sync_alerts.cluster", "sync_alerts", cluster_id=2),
    M("list_decisions.defaults", "list_decisions", 2),
    M("list_decisions.limit", "list_decisions", 2, limit=5),
    M("get_preferences", "get_preferences"),
    M("update_preferences.drops_none", "update_preferences", theme="dark", units=None, dry_run_global=False),
    M("list_vacation", "list_vacation"),
    M("add_vacation.defaults", "add_vacation", 100, 200),
    M("add_vacation.all", "add_vacation", 100, 200, contact_email="a@example.com", notes="n"),
    M("update_vacation.none", "update_vacation", 8),
    M("update_vacation.all", "update_vacation", 8, starts_at=1, ends_at=2, contact_email="", notes="x"),
    M("delete_vacation", "delete_vacation", 8),
    M("list_windows", "list_windows", 2),
    M("add_window.defaults", "add_window", 2, 6, 9),
    M("add_window.all", "add_window", 2, 22, 2, weekday_mask=3, label="night"),
    M("update_window.none", "update_window", 2, 3),
    M("update_window.all", "update_window", 2, 3, start_hour=0, end_hour=0, weekday_mask=1, label=""),
    M("delete_window", "delete_window", 2, 3),
    # ── response handling of _request ──
    M("error.404_detail", "health", response=(404, {"detail": "Nope"})),
    M("error.500_plain_text", "health", response=Raw(500, "oops", "text/plain")),
    M("error.connect", "health", response=Raise("ConnectError", "refused")),
    M("ok.csv_becomes_dict", "health", response=(200, "x\n")),
    M("ok.list_body", "health", response=(200, [1, 2])),
    M("ok.rich_body_parsed", "health", response=(200, DEFAULT_BODY)),
]

# IrrigationClient construction (base URL, timeout, token and extra headers).
CONSTRUCTOR_CASES: dict[str, dict[str, Any]] = {
    "explicit_token": {"kwargs": {"token": "tok"}},
    "empty_token_suppresses_stored": {"kwargs": {"token": ""}, "token_file": "stored"},
    "none_token_reads_file": {"kwargs": {"token": None}, "token_file": "stored"},
    "none_token_reads_env": {"kwargs": {}, "env": {"GREENHOUSE_API_TOKEN": "env-tok"}, "token_file": "stored"},
    "headers_merged_token_wins": {"kwargs": {"token": "tok", "headers": {"X-Trace": "1", "Authorization": "Basic x"}}},
    "headers_none": {"kwargs": {"token": None, "headers": None}},
    "default_base_url": {"kwargs": {"token": ""}, "default_base_url": True},
}


# ─────────────────────────── recording harness ───────────────────────────


class Recorder:
    """Serves canned responses and records every client construction and request."""

    def __init__(self, routes: dict[tuple[str, str], Any] | None = None, default: Any = None):
        self.routes = routes or {}
        self.default = default  # None → echo body
        self.requests: list[dict[str, Any]] = []
        self.clients: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        content = request.content.decode("utf-8") if request.content else None
        try:
            parsed = json.loads(content) if content else None
        except json.JSONDecodeError:
            parsed = "<not json>"
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "path": request.url.path,
                "raw_path": request.url.raw_path.decode("ascii"),
                "query": [list(pair) for pair in request.url.params.multi_items()],
                "content": content,
                "json": parsed,
                "authorization": request.headers.get("authorization"),
                "content_type": request.headers.get("content-type"),
            }
        )
        spec = self.routes.get((request.method, request.url.path), self.default)
        if spec is None:
            spec = (200, {"echo": f"{request.method} {request.url.path}", "ok": True})
        return _respond(spec, request)


def _respond(spec: Any, request: httpx.Request) -> httpx.Response:
    if isinstance(spec, Raise):
        raise getattr(httpx, spec.exc)(spec.message, request=request)
    if isinstance(spec, Raw):
        return httpx.Response(spec.status, content=spec.content.encode(), headers={"content-type": spec.content_type})
    status, body = spec
    if isinstance(body, str):
        return httpx.Response(status, text=body, headers={"content-type": "text/csv"})
    return httpx.Response(status, json=body)


@pytest.fixture
def recorder_slot(monkeypatch):
    """Patch ``httpx.Client.__init__`` to inject the current recorder's MockTransport (all else untouched)."""
    slot: dict[str, Recorder] = {}
    original_init = httpx.Client.__init__

    def patched_init(self, *args, **kwargs):
        rec = slot["current"]
        headers = kwargs.get("headers") or {}
        rec.clients.append(
            {
                "base_url": str(kwargs.get("base_url")),
                "timeout": kwargs.get("timeout"),
                "headers": dict(sorted(headers.items())),
            }
        )
        kwargs.setdefault("transport", httpx.MockTransport(rec.handle))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", patched_init)
    return slot


@contextlib.contextmanager
def _case_dir(base: Path, name: str):
    path = base / name.replace("/", "_")
    path.mkdir(parents=True)
    with contextlib.chdir(path):
        yield path


def _write_token(home: Path, content: str | None, xdg: bool) -> Path:
    token_path = (home / "config" if xdg else home / ".config") / "greenhouse" / "token"
    if content is not None:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(content, encoding="utf-8")
    return token_path


def _token_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return {"content": path.read_text(encoding="utf-8"), "mode": oct(stat.S_IMODE(path.stat().st_mode))}


def _scrub(text: str, tmp: Path) -> str:
    return text.replace(str(tmp), "<TMP>")


runner = CliRunner()


def run_cli_case(case: Case, base: Path, slot: dict[str, Recorder]) -> dict[str, Any]:
    rec = Recorder(case.routes)
    slot["current"] = rec
    with _case_dir(base, "cli-" + case.id) as home:
        token_path = _write_token(home, case.token_file, case.xdg)
        if case.token_file is not None:
            os.chmod(token_path, 0o644)
        env: dict[str, str | None] = {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "config") if case.xdg else None,
            **case.env,
        }
        rich._console = None  # fresh global console per case (it caches width/stream)
        result = runner.invoke(app, list(case.argv), input=case.input, env=env, prog_name=PROG)
        exc = result.exception
        entry = {
            "argv": [PROG, *case.argv],
            "env": case.env,
            "input": case.input,
            "token_file_before": case.token_file,
            "clients": rec.clients,
            "requests": rec.requests,
            "exit_code": result.exit_code,
            "exception": None if exc is None or isinstance(exc, SystemExit) else type(exc).__name__,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "token_file_after": _token_state(token_path),
            "files": {name: Path(name).read_text(encoding="utf-8") for name in case.files if Path(name).exists()},
        }
        return json.loads(_scrub(json.dumps(entry, ensure_ascii=False), home))


def _call_client(client: IrrigationClient, case: ClientCase) -> dict[str, Any]:
    try:
        value = getattr(client, case.method)(*case.args, **case.kwargs)
    except ServerError as e:
        return {"server_error": {"status_code": e.status_code, "detail": e.detail, "str": str(e)}}
    return {"returned": value}


def run_client_case(case: ClientCase, slot: dict[str, Recorder]) -> dict[str, Any]:
    rec = Recorder(default=case.response)
    slot["current"] = rec
    client = IrrigationClient(base_url="http://client.example:8123", token="tok-direct")
    outcome = _call_client(client, case)
    return {
        "call": {"method": case.method, "args": list(case.args), "kwargs": case.kwargs},
        "requests": rec.requests,
        **outcome,
    }


def run_constructor_case(name: str, spec: dict[str, Any], base: Path, slot: dict[str, Recorder]) -> dict[str, Any]:
    rec = Recorder()
    slot["current"] = rec
    with _case_dir(base, "ctor-" + name) as home:
        _write_token(home, spec.get("token_file"), xdg=True)
        saved = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "GREENHOUSE_API_TOKEN")}
        os.environ.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"), **spec.get("env", {})})
        try:
            kwargs = dict(spec["kwargs"])
            if not spec.get("default_base_url"):
                kwargs["base_url"] = "http://client.example:8123"
            client = IrrigationClient(**kwargs)
            client.health()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    return {"spec": spec, "clients": rec.clients, "requests": rec.requests}


def build_all(base: Path, slot: dict[str, Recorder]) -> dict[str, Any]:
    return {
        "cli": {case.id: run_cli_case(case, base, slot) for case in CLI_CASES},
        "client": {case.id: run_client_case(case, slot) for case in CLIENT_CASES},
        "client_constructor": {
            name: run_constructor_case(name, spec, base, slot) for name, spec in CONSTRUCTOR_CASES.items()
        },
    }


def _golden_entry(section: str, key: str) -> Any:
    path = GOLDEN_DIR / GOLDEN
    if not path.exists():
        pytest.fail(f"golden file missing: {path} (create it with {UPDATE_ENV_VAR}=1)")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert key in data[section], f"no golden entry for {section}/{key} (regenerate with {UPDATE_ENV_VAR}=1)"
    return data[section][key]


def _command_of(argv: tuple[str, ...], leaves: set[tuple[str, ...]]) -> tuple[str, ...] | None:
    rest = list(argv)
    if rest[:1] == ["--server"]:
        rest = rest[2:]
    matches = [leaf for leaf in leaves if tuple(rest[: len(leaf)]) == leaf]
    return max(matches, key=len) if matches else None


# ─────────────────────────── tests ───────────────────────────


def test_case_ids_are_unique():
    assert len({c.id for c in CLI_CASES}) == len(CLI_CASES)
    assert len({c.id for c in CLIENT_CASES}) == len(CLIENT_CASES)


def test_every_cli_command_has_a_case():
    """Derived from the live Typer tree: adding a command without a golden case fails here."""
    leaves = set(leaf_command_paths())
    covered = {_command_of(c.argv, leaves) for c in CLI_CASES} - {None}
    missing = sorted(" ".join(p) for p in leaves - covered)
    assert not missing, f"CLI commands without a contract case: {missing}"


def test_every_client_method_has_a_case():
    public = {name for name, _ in inspect.getmembers(IrrigationClient, inspect.isfunction) if not name.startswith("_")}
    covered = {c.method for c in CLIENT_CASES}
    assert public - covered == set(), f"IrrigationClient methods without a case: {sorted(public - covered)}"
    assert covered - public == set()


def test_requests_and_output_golden(recorder_slot, tmp_path):
    """Single writer of the golden: every CLI case, client method case and constructor case."""
    assert_golden_json(GOLDEN, build_all(tmp_path / "cases", recorder_slot))


@pytest.mark.parametrize("case", CLI_CASES, ids=lambda c: c.id)
def test_cli_case(recorder_slot, tmp_path, case):
    if os.environ.get(UPDATE_ENV_VAR) == "1":
        pytest.skip("golden is (re)written by test_requests_and_output_golden")
    actual = run_cli_case(case, tmp_path, recorder_slot)
    assert to_canonical_json(actual) == to_canonical_json(_golden_entry("cli", case.id))


@pytest.mark.parametrize("case", CLIENT_CASES, ids=lambda c: c.id)
def test_client_case(recorder_slot, case):
    if os.environ.get(UPDATE_ENV_VAR) == "1":
        pytest.skip("golden is (re)written by test_requests_and_output_golden")
    actual = run_client_case(case, recorder_slot)
    assert to_canonical_json(actual) == to_canonical_json(_golden_entry("client", case.id))


def test_exit_code_contract(recorder_slot, tmp_path):
    """Readable summary of the exit-code contract (the golden holds the exact bytes)."""
    by_id = {c.id: c for c in CLI_CASES}
    expected = {
        "health": 0,
        "error.404_detail": 1,
        "error.connection_refused": 1,
        "check.no_target_exits_1": 1,
        "check.has_alerts_exits_2": 2,
        "check.alerts_win_over_error_exits_2": 2,
        "monitor.needs_water_exits_2": 2,
        "irrigate.action_error_exits_1": 1,
        "usage.unknown_command": 2,
        "cluster.delete.prompt_declined": 1,
        "login.failure_exits_1": 1,
    }
    for case_id, code in expected.items():
        entry = run_cli_case(by_id[case_id], tmp_path, recorder_slot)
        assert entry["exit_code"] == code, case_id
    refused = run_cli_case(by_id["error.connection_refused"], tmp_path / "again", recorder_slot)
    assert refused["stderr"] == "Error: Cannot connect to server: [Errno 111] Connection refused\n"
    assert refused["stdout"] == ""


def test_real_connection_refused(monkeypatch):
    """No mock: a refused TCP connect surfaces as ``Error: Cannot connect to server: …`` and exit 1."""
    monkeypatch.setenv("IRRIGATION_SERVER_URL", "http://127.0.0.1:1")
    result = runner.invoke(app, ["health"], prog_name=PROG)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith("Error: Cannot connect to server: ")
