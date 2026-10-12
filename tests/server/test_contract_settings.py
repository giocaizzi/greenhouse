"""Characterization: server ``Settings`` and every environment variable the code reads.

What is pinned here (and NOT already pinned by ``test_scheduler_settings.py``, which
only checks that validation errors *mention* the env var):

- ``Settings.model_json_schema()`` and the per-field table (declaration order,
  annotation, default, alias choices) — ``contracts/settings_schema.json``;
- the env name every field is read from, case-insensitivity, ``.env`` loading,
  precedence (kwargs > env > ``.env`` > default), ``extra="ignore"``;
- the *exact* startup validation messages for ``IRRIGATION_SYNC_INTERVAL_MINUTES`` and
  ``IRRIGATION_CHECK_CRON_HOURS`` (``contracts/settings_validation_errors.json``); the
  removed legacy ``IRRIGATION_CHECK_INTERVAL_HOURS`` (OD3) is ignored like any unknown var;
- the 8 env vars read outside ``Settings`` (``TUYA_*``, ``IRRIGATION_PLANT_DB_PATH``,
  ``IRRIGATION_TZ``, ``IRRIGATION_DB_URL`` in the migrations env,
  ``IRRIGATION_SERVER_URL``, ``GREENHOUSE_API_TOKEN``, plus ``XDG_CONFIG_HOME``): a
  static golden of every ``os.environ`` / ``os.getenv`` read in ``libs/``
  (``contracts/env_reads.json``) and behavior tests where they are cheap.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
import tinytuya
from pydantic import AliasChoices, ValidationError

from golden import assert_golden_json
from greenhouse_cli.client import _default_token_path, load_stored_token
from greenhouse_cli.commands._helpers import get_client
from greenhouse_cli.commands.auth import _login_client
from greenhouse_core import utils
from greenhouse_core.devices import gateway as gateway_mod
from greenhouse_server.config import Settings
from greenhouse_server.scheduler import _resolve_check_cron_hours
from greenhouse_server.services.notify import NtfyClient
from server.conftest import TEST_MCP_TOKEN

if TYPE_CHECKING:
    import typer

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBS = REPO_ROOT / "libs"
DEFAULT_PORT = 8000


def _settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)


# --- schema ------------------------------------------------------------------


@pytest.mark.usefixtures("clean_env")
def test_settings_schema_and_field_table_match_golden() -> None:
    """Settings schema and field table match golden."""
    fields = []
    for name, info in Settings.model_fields.items():
        alias = info.validation_alias
        assert alias is None or isinstance(alias, AliasChoices)
        fields.append(
            {
                "name": name,
                "annotation": str(info.annotation).replace("<class '", "").replace("'>", ""),
                "default": info.default,
                "alias": info.alias,
                "validation_alias_choices": list(alias.choices) if alias is not None else None,
            }
        )
    assert_golden_json(
        "contracts/settings_schema.json",
        {"fields": fields, "json_schema": Settings.model_json_schema()},
    )


@pytest.mark.dotenv
def test_settings_model_config_explicit_keys() -> None:
    """Settings model config explicit keys."""
    config = Settings.model_config
    assert config["env_prefix"] == "IRRIGATION_"
    assert config["env_file"] == ".env"
    assert config["case_sensitive"] is False
    assert config["extra"] == "ignore"
    assert config["populate_by_name"] is False


@pytest.mark.usefixtures("clean_env")
def test_settings_defaults_with_empty_environment() -> None:
    """Settings defaults with empty environment."""
    s = _settings()
    assert s.model_dump() == {
        "db_url": "sqlite:///data/irrigation.db",
        "host": "127.0.0.1",
        "port": 8000,
        "debug": False,
        "plant_db_path": None,
        "weather_lat": 45.464,
        "weather_lon": 9.189,
        "sync_interval_minutes": 180,
        "check_cron_hours": "*",
        "enable_scheduler": True,
        "mcp_token": None,
        "ntfy_server_url": None,
        "ntfy_topic": None,
        "ntfy_token": None,
        "auth_enabled": True,
        "auth_secret_key": None,
        "auth_token_ttl_minutes": 1440,
        "auth_cookie_name": "greenhouse_session",
        "auth_cookie_secure": False,
        "auth_admin_username": None,
        "auth_admin_password": None,
        "pump_watcher_enabled": True,
        "pump_watcher_poll_seconds": 2.0,
        "pump_watcher_warmup_seconds": 5.0,
        "pump_watcher_max_read_failures": 5,
    }


# --- env names ---------------------------------------------------------------

# (env var, raw value, field, parsed value) — one row per Settings field.
ENV_TABLE = [
    ("IRRIGATION_DB_URL", "sqlite:///x.db", "db_url", "sqlite:///x.db"),
    ("IRRIGATION_HOST", "0.0.0.0", "host", "0.0.0.0"),  # noqa: S104 — the Docker opt-in value
    ("IRRIGATION_PORT", "9123", "port", 9123),
    ("IRRIGATION_DEBUG", "true", "debug", True),
    ("IRRIGATION_PLANT_DB_PATH", "/srv/plants.json", "plant_db_path", "/srv/plants.json"),
    ("IRRIGATION_WEATHER_LAT", "1.5", "weather_lat", 1.5),
    ("IRRIGATION_WEATHER_LON", "-2.25", "weather_lon", -2.25),
    ("IRRIGATION_SYNC_INTERVAL_MINUTES", "30", "sync_interval_minutes", 30),
    ("IRRIGATION_CHECK_CRON_HOURS", "0,12", "check_cron_hours", "0,12"),
    ("IRRIGATION_ENABLE_SCHEDULER", "false", "enable_scheduler", False),
    ("GREENHOUSE_MCP_TOKEN", "mcp-tok", "mcp_token", "mcp-tok"),
    ("GREENHOUSE_NTFY_SERVER_URL", "https://ntfy.invalid", "ntfy_server_url", "https://ntfy.invalid"),
    ("GREENHOUSE_NTFY_TOPIC", "topic", "ntfy_topic", "topic"),
    ("GREENHOUSE_NTFY_TOKEN", "ntfy-tok", "ntfy_token", "ntfy-tok"),
    ("IRRIGATION_AUTH_ENABLED", "0", "auth_enabled", False),
    ("GREENHOUSE_AUTH_SECRET_KEY", "secret", "auth_secret_key", "secret"),
    ("IRRIGATION_AUTH_TOKEN_TTL_MINUTES", "15", "auth_token_ttl_minutes", 15),
    ("IRRIGATION_AUTH_COOKIE_NAME", "gh", "auth_cookie_name", "gh"),
    ("IRRIGATION_AUTH_COOKIE_SECURE", "yes", "auth_cookie_secure", True),
    ("GREENHOUSE_AUTH_ADMIN_USERNAME", "admin", "auth_admin_username", "admin"),
    ("GREENHOUSE_AUTH_ADMIN_PASSWORD", "pw", "auth_admin_password", "pw"),
    ("IRRIGATION_PUMP_WATCHER_ENABLED", "off", "pump_watcher_enabled", False),
    ("IRRIGATION_PUMP_WATCHER_POLL_SECONDS", "0.5", "pump_watcher_poll_seconds", 0.5),
    ("IRRIGATION_PUMP_WATCHER_WARMUP_SECONDS", "7", "pump_watcher_warmup_seconds", 7.0),
    ("IRRIGATION_PUMP_WATCHER_MAX_READ_FAILURES", "9", "pump_watcher_max_read_failures", 9),
]


def test_env_table_covers_every_field() -> None:
    """Env table covers every field."""
    assert [row[2] for row in ENV_TABLE] == list(Settings.model_fields)


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(("env", "raw", "field", "expected"), ENV_TABLE, ids=[r[0] for r in ENV_TABLE])
def test_each_field_reads_its_env_var(
    monkeypatch: pytest.MonkeyPatch, env: str, raw: str, field: str, expected: object
) -> None:
    """Each field reads its env var."""
    monkeypatch.setenv(env, raw)
    assert getattr(_settings(), field) == expected


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(("env", "raw", "field", "expected"), ENV_TABLE, ids=[r[0] for r in ENV_TABLE])
def test_env_names_are_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, env: str, raw: str, field: str, expected: object
) -> None:
    """Env names are case insensitive."""
    monkeypatch.setenv(env.lower(), raw)
    assert getattr(_settings(), field) == expected


ALIASED_FIELDS = [
    ("MCP_TOKEN", "mcp_token"),
    ("NTFY_SERVER_URL", "ntfy_server_url"),
    ("NTFY_TOPIC", "ntfy_topic"),
    ("NTFY_TOKEN", "ntfy_token"),
    ("AUTH_SECRET_KEY", "auth_secret_key"),
    ("AUTH_ADMIN_USERNAME", "auth_admin_username"),
    ("AUTH_ADMIN_PASSWORD", "auth_admin_password"),
]


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(("suffix", "field"), ALIASED_FIELDS, ids=[r[0] for r in ALIASED_FIELDS])
def test_aliased_fields_read_only_the_greenhouse_env_name(
    monkeypatch: pytest.MonkeyPatch, suffix: str, field: str
) -> None:
    """An aliased secret (MCP token, ntfy, auth secret/admin) is read from ``GREENHOUSE_X`` only.

    The bare ``X`` and the prefixed ``IRRIGATION_X`` used to configure it too (owner decision:
    one documented name per secret, so a stray ``MCP_TOKEN`` in the environment cannot open MCP).
    """
    monkeypatch.setenv(f"IRRIGATION_{suffix}", "https://irrigation.invalid")
    monkeypatch.setenv(suffix, "https://bare.invalid")
    assert getattr(_settings(), field) is None
    monkeypatch.setenv(f"GREENHOUSE_{suffix}", "https://greenhouse.invalid")
    assert getattr(_settings(), field) == "https://greenhouse.invalid"


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(("suffix", "field"), ALIASED_FIELDS, ids=[r[0] for r in ALIASED_FIELDS])
def test_aliased_fields_ignore_old_names_in_dotenv(tmp_path: Path, suffix: str, field: str) -> None:
    """Aliased fields ignore old names in dotenv."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"IRRIGATION_{suffix}=https://a.invalid\n{suffix}=https://b.invalid\n")
    assert getattr(Settings(_env_file=env_file), field) is None
    env_file.write_text(f"GREENHOUSE_{suffix}=https://c.invalid\n")
    assert getattr(Settings(_env_file=env_file), field) == "https://c.invalid"


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(("suffix", "field"), ALIASED_FIELDS, ids=[r[0] for r in ALIASED_FIELDS])
def test_aliased_fields_ignore_double_prefixed_name(monkeypatch: pytest.MonkeyPatch, suffix: str, field: str) -> None:
    """Aliased fields ignore double prefixed name."""
    monkeypatch.setenv(f"IRRIGATION_GREENHOUSE_{suffix}", "ignored")
    assert getattr(_settings(), field) is None


@pytest.mark.usefixtures("clean_env")
def test_aliased_fields_accept_both_kwarg_spellings() -> None:
    """Aliased fields accept both kwarg spellings."""
    assert _settings(mcp_token=TEST_MCP_TOKEN).mcp_token == TEST_MCP_TOKEN
    assert _settings(GREENHOUSE_MCP_TOKEN=TEST_MCP_TOKEN).mcp_token == TEST_MCP_TOKEN


# --- sources, precedence, extras -------------------------------------------


@pytest.mark.dotenv
def test_dotenv_in_cwd_is_loaded_and_env_beats_it(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dotenv in cwd is loaded and env beats it."""
    (clean_env / ".env").write_text(
        "IRRIGATION_PORT=9001\nGREENHOUSE_MCP_TOKEN=dotenv-token\nIRRIGATION_CHECK_CRON_HOURS=*\nIRRIGATION_NOT_A_FIELD=1\n"
        "UNRELATED=1\n",
        encoding="utf-8",
    )
    s = Settings()
    assert (s.port, s.mcp_token, s.check_cron_hours) == (9001, "dotenv-token", "*")
    assert Settings(_env_file=None).port == DEFAULT_PORT
    env_port, explicit_port = 9002, 9003
    monkeypatch.setenv("IRRIGATION_PORT", str(env_port))
    assert Settings().port == env_port
    assert Settings(port=explicit_port).port == explicit_port


@pytest.mark.usefixtures("clean_env")
def test_extra_inputs_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extra inputs are ignored."""
    monkeypatch.setenv("IRRIGATION_NOT_A_FIELD", "1")
    s = _settings(not_a_field=1)
    assert not hasattr(s, "not_a_field")


@pytest.mark.usefixtures("clean_env")
def test_removed_check_interval_hours_is_ignored(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """OD3: ``IRRIGATION_CHECK_INTERVAL_HOURS`` is gone — ignored like any unknown var, never validated."""
    monkeypatch.setenv("IRRIGATION_CHECK_INTERVAL_HOURS", "5")  # used to fail startup (5 does not divide 24)
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.scheduler"):
        s = _settings()
        assert not hasattr(s, "check_interval_hours")
        assert _resolve_check_cron_hours(s) == "*"
        assert _resolve_check_cron_hours(_settings(check_cron_hours="0,12")) == "0,12"
    assert caplog.records == []


# --- exact validation messages ----------------------------------------------

VALIDATION_CASES: dict[str, dict[str, object]] = {
    "sync_interval=0": {"sync_interval_minutes": 0},
    "sync_interval=-1": {"sync_interval_minutes": -1},
    "cron=bogus": {"check_cron_hours": "bogus"},
    "cron=25": {"check_cron_hours": "25"},
    "cron=*/0": {"check_cron_hours": "*/0"},
    "cron=empty": {"check_cron_hours": ""},
    "cron=0,6,": {"check_cron_hours": "0,6,"},
    "ntfy=file-scheme": {"ntfy_server_url": "file:///etc/passwd"},
    "ntfy=ftp-scheme": {"ntfy_server_url": "ftp://ntfy.invalid"},
    "ntfy=no-host": {"ntfy_server_url": "https://"},
    "ntfy=no-scheme": {"ntfy_server_url": "ntfy.invalid"},
    "ntfy=port-only": {"ntfy_server_url": "http://:80"},
    "ntfy=credentials-redacted": {"ntfy_server_url": "ftp://user:pw@ntfy.invalid/topic"},
}


def _errors(exc: ValidationError) -> list[dict[str, object]]:
    return [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in exc.errors()]


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize("url", ["https://ntfy.invalid", "http://192.0.2.10:8080/", "HTTPS://ntfy.invalid", "", None])
def test_ntfy_server_url_accepts_http_https_and_unset(url: str | None) -> None:
    """Ntfy server URL accepts HTTP https and unset."""
    assert _settings(ntfy_server_url=url).ntfy_server_url == url


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize("raw", ["", " ", "\t\n"])
def test_blank_mcp_token_is_unset(raw: str) -> None:
    """Blank MCP token is unset."""
    assert _settings(mcp_token=raw).mcp_token is None


@pytest.mark.usefixtures("clean_env")
def test_ntfy_url_error_never_echoes_credentials() -> None:
    """Ntfy URL error never echoes credentials."""
    with pytest.raises(ValidationError) as exc:
        _settings(ntfy_server_url="file://admin:hunter2@ntfy.invalid/x")
    assert "hunter2" not in str(exc.value)
    assert "admin" not in str(exc.value)


def test_ntfy_client_refuses_a_non_http_scheme() -> None:
    """Ntfy client refuses a non HTTP scheme."""
    with pytest.raises(ValueError, match="http:// or https://"):
        NtfyClient("file:///tmp", "topic")


@pytest.mark.usefixtures("clean_env")
def test_startup_validation_messages_match_golden() -> None:
    """Startup validation messages match golden."""
    observed: dict[str, object] = {}
    for case, kwargs in VALIDATION_CASES.items():
        with pytest.raises(ValidationError) as exc:
            _settings(**kwargs)
        observed[case] = _errors(exc.value)
    assert_golden_json("contracts/settings_validation_errors.json", observed)


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(
    ("env", "raw", "kwarg"),
    [
        ("IRRIGATION_SYNC_INTERVAL_MINUTES", "0", {"sync_interval_minutes": 0}),
        ("IRRIGATION_CHECK_CRON_HOURS", "bogus", {"check_cron_hours": "bogus"}),
    ],
)
def test_env_sourced_values_fail_with_the_same_message_as_kwargs(
    monkeypatch: pytest.MonkeyPatch, env: str, raw: str, kwarg: dict[str, object]
) -> None:
    """Env sourced values fail with the same message as kwargs."""
    with pytest.raises(ValidationError) as from_kwarg:
        _settings(**kwarg)
    monkeypatch.setenv(env, raw)
    with pytest.raises(ValidationError) as from_env:
        _settings()
    assert [e["msg"] for e in from_env.value.errors()] == [e["msg"] for e in from_kwarg.value.errors()]


# --- env vars read outside Settings -----------------------------------------


def _literal(node: ast.AST | None) -> object:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except ValueError:
        return f"<expr: {ast.unparse(node)}>"


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _env_read(node: ast.AST, module: str) -> dict[str, Any] | None:
    """The env read ``node`` performs (``os.environ.get`` / ``os.getenv`` / ``os.environ[...]``), or ``None``."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        func = node.func
        if func.attr == "get" and _is_os_environ(func.value):
            call = "os.environ.get"
        elif func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
            call = "os.getenv"
        else:
            return None
        default = node.args[1] if len(node.args) > 1 else None
        return {"module": module, "call": call, "var": _literal(node.args[0]), "default": _literal(default)}
    if isinstance(node, ast.Subscript) and _is_os_environ(node.value):
        return {"module": module, "call": "os.environ[]", "var": _literal(node.slice), "default": None}
    return None


def _scan_env_reads() -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    for path in sorted(LIBS.glob("*/greenhouse_*/**/*.py")):
        package_root = path.relative_to(LIBS).parts[0]
        module = ".".join(path.relative_to(LIBS / package_root).with_suffix("").parts)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            read = _env_read(node, module)
            if read is not None:
                reads.append(read)
    return sorted(reads, key=lambda r: (r["module"], r["var"], r["call"], repr(r["default"])))


def test_direct_env_reads_match_golden() -> None:
    """Every ``os.environ`` / ``os.getenv`` read in libs/: module, variable and default."""
    assert_golden_json("contracts/env_reads.json", _scan_env_reads())


@pytest.mark.usefixtures("clean_env")
def test_tuya_env_vars_feed_device_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tuya env vars feed device gateway."""
    calls: list[dict[str, object]] = []

    def cloud(**kw: object) -> object:
        calls.append(kw)
        return object()

    monkeypatch.setattr(tinytuya, "Cloud", cloud)  # the module gateway_mod.tinytuya names
    with pytest.raises(ValueError, match=r"^Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET$"):
        gateway_mod.DeviceGateway()
    monkeypatch.setenv("TUYA_CLIENT_ID", "fake-client")
    monkeypatch.setenv("TUYA_CLIENT_SECRET", "fake-secret")
    gw = gateway_mod.DeviceGateway()
    assert (gw.client_id, gw.client_secret, gw.region) == ("fake-client", "fake-secret", "eu")
    monkeypatch.setenv("TUYA_REGION", "us")
    assert gateway_mod.DeviceGateway().region == "us"
    assert calls == [
        {"apiRegion": "eu", "apiKey": "fake-client", "apiSecret": "fake-secret"},
        {"apiRegion": "us", "apiKey": "fake-client", "apiSecret": "fake-secret"},
    ]


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(("value", "expect_default"), [(None, True), ("", True), ("/srv/custom.json", False)])
def test_plant_db_path_env_is_read_at_import_time(value: str | None, *, expect_default: bool) -> None:
    """``plant_db.PLANT_DB_PATH`` is computed when the module is first imported."""
    env = dict(os.environ)
    if value is not None:
        env["IRRIGATION_PLANT_DB_PATH"] = value
    out = subprocess.run(
        [sys.executable, "-c", "import greenhouse_core.plant_db as p; print(p.PLANT_DB_PATH)"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if expect_default:
        assert Path(out).parts[-3:] == ("greenhouse_core", "data", "plant_database.json")
    else:
        assert out == "/srv/custom.json"


@pytest.mark.usefixtures("clean_env")
def test_irrigation_tz_is_display_timezone_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Irrigation TZ is display timezone fallback."""
    monkeypatch.setattr(utils, "_display_timezone", None)
    monkeypatch.delenv("IRRIGATION_TZ", raising=False)
    assert utils.get_display_timezone() == "UTC"
    monkeypatch.setenv("IRRIGATION_TZ", "Europe/Rome")
    assert utils.get_display_timezone() == "Europe/Rome"
    monkeypatch.setattr(utils, "_display_timezone", "Asia/Tokyo")
    assert utils.get_display_timezone() == "Asia/Tokyo"


@pytest.mark.usefixtures("clean_env")
def test_cli_server_url_env_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI server URL env and default."""
    ctx = cast("typer.Context", SimpleNamespace(obj=None))  # duck-typed: only ctx.obj is read
    assert str(get_client(ctx).http.base_url) == "http://localhost:8000"
    assert str(_login_client(ctx).http.base_url) == "http://localhost:8000"
    monkeypatch.setenv("IRRIGATION_SERVER_URL", "http://greenhouse.invalid:9")
    assert str(get_client(ctx).http.base_url) == "http://greenhouse.invalid:9"
    assert str(_login_client(ctx).http.base_url) == "http://greenhouse.invalid:9"
    assert (
        str(get_client(cast("typer.Context", SimpleNamespace(obj="http://flag.invalid"))).http.base_url)
        == "http://flag.invalid"
    )


def test_cli_api_token_env_and_token_file(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI API token env and token file."""
    assert _default_token_path() == clean_env / "config" / "greenhouse" / "token"
    assert load_stored_token() is None
    monkeypatch.setenv("GREENHOUSE_API_TOKEN", "  env-token  ")
    assert load_stored_token() == "env-token"
    monkeypatch.setenv("GREENHOUSE_API_TOKEN", "   ")
    assert load_stored_token() is None
    monkeypatch.delenv("GREENHOUSE_API_TOKEN")
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert _default_token_path() == clean_env / ".config" / "greenhouse" / "token"


def test_env_read_scan_finds_exactly_the_documented_vars() -> None:
    """The scan sees the 8 documented non-Settings vars plus ``XDG_CONFIG_HOME`` (guards an empty scan)."""
    reads = _scan_env_reads()
    assert {r["var"] for r in reads} == {
        "GREENHOUSE_API_TOKEN",
        "IRRIGATION_DB_URL",
        "IRRIGATION_PLANT_DB_PATH",
        "IRRIGATION_SERVER_URL",
        "IRRIGATION_TZ",
        "TUYA_CLIENT_ID",
        "TUYA_CLIENT_SECRET",
        "TUYA_REGION",
        "XDG_CONFIG_HOME",
    }
