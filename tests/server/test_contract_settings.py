"""Characterization: server ``Settings`` and every environment variable the code reads (G8).

What is pinned here (and NOT already pinned by ``test_scheduler_settings.py``, which
only checks that validation errors *mention* the env var):

- ``Settings.model_json_schema()`` and the per-field table (declaration order,
  annotation, default, alias choices) — ``contracts/settings_schema.json``;
- the env name every field is read from, case-insensitivity, ``.env`` loading,
  precedence (kwargs > env > ``.env`` > default), ``extra="ignore"``;
- the *exact* startup validation messages for ``IRRIGATION_SYNC_INTERVAL_MINUTES``,
  ``IRRIGATION_CHECK_CRON_HOURS`` and the legacy ``IRRIGATION_CHECK_INTERVAL_HOURS``
  (``contracts/settings_validation_errors.json``) and the two runtime warnings logged
  by ``scheduler._resolve_check_cron_hours``;
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

import pytest
from pydantic import ValidationError

from golden import assert_golden_json
from greenhouse_server.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBS = REPO_ROOT / "libs"


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


# --- schema ------------------------------------------------------------------


def test_settings_schema_and_field_table_match_golden(clean_env):
    fields = []
    for name, info in Settings.model_fields.items():
        alias = info.validation_alias
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


def test_settings_model_config_explicit_keys():
    config = Settings.model_config
    assert config["env_prefix"] == "IRRIGATION_"
    assert config["env_file"] == ".env"
    assert config["case_sensitive"] is False
    assert config["extra"] == "ignore"
    assert config["populate_by_name"] is True


def test_settings_defaults_with_empty_environment(clean_env):
    s = _settings()
    assert s.model_dump() == {
        "db_url": "sqlite:///data/irrigation.db",
        "host": "0.0.0.0",
        "port": 8000,
        "debug": False,
        "plant_db_path": None,
        "weather_lat": 45.464,
        "weather_lon": 9.189,
        "sync_interval_minutes": 180,
        "check_cron_hours": "*",
        "check_interval_hours": None,
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
    assert s.check_cron_hours_explicit is False


# --- env names ---------------------------------------------------------------

# (env var, raw value, field, parsed value) — one row per Settings field.
ENV_TABLE = [
    ("IRRIGATION_DB_URL", "sqlite:///x.db", "db_url", "sqlite:///x.db"),
    ("IRRIGATION_HOST", "127.0.0.1", "host", "127.0.0.1"),
    ("IRRIGATION_PORT", "9123", "port", 9123),
    ("IRRIGATION_DEBUG", "true", "debug", True),
    ("IRRIGATION_PLANT_DB_PATH", "/srv/plants.json", "plant_db_path", "/srv/plants.json"),
    ("IRRIGATION_WEATHER_LAT", "1.5", "weather_lat", 1.5),
    ("IRRIGATION_WEATHER_LON", "-2.25", "weather_lon", -2.25),
    ("IRRIGATION_SYNC_INTERVAL_MINUTES", "30", "sync_interval_minutes", 30),
    ("IRRIGATION_CHECK_CRON_HOURS", "0,12", "check_cron_hours", "0,12"),
    ("IRRIGATION_CHECK_INTERVAL_HOURS", "6", "check_interval_hours", 6),
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


def test_env_table_covers_every_field():
    assert [row[2] for row in ENV_TABLE] == list(Settings.model_fields)


@pytest.mark.parametrize(("env", "raw", "field", "expected"), ENV_TABLE, ids=[r[0] for r in ENV_TABLE])
def test_each_field_reads_its_env_var(clean_env, monkeypatch, env, raw, field, expected):
    monkeypatch.setenv(env, raw)
    assert getattr(_settings(), field) == expected


@pytest.mark.parametrize(("env", "raw", "field", "expected"), ENV_TABLE, ids=[r[0] for r in ENV_TABLE])
def test_env_names_are_case_insensitive(clean_env, monkeypatch, env, raw, field, expected):
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


@pytest.mark.parametrize(("suffix", "field"), ALIASED_FIELDS, ids=[r[0] for r in ALIASED_FIELDS])
def test_aliased_fields_current_behavior_read_three_env_names(clean_env, monkeypatch, suffix, field):
    """Pins current (surprising) behavior: an ``AliasChoices("GREENHOUSE_X", "x")`` field
    is read from **three** env names — ``GREENHOUSE_X`` (documented), the bare ``X``
    and the prefixed ``IRRIGATION_X`` (pydantic-settings applies ``env_prefix`` to the
    bare-name choice and also matches it unprefixed). So e.g. ``MCP_TOKEN`` or
    ``IRRIGATION_AUTH_SECRET_KEY`` in the environment configure the server — see
    REFACTOR_NOTES.md. Precedence: ``GREENHOUSE_X`` > ``X`` > ``IRRIGATION_X``.
    """
    monkeypatch.setenv(f"IRRIGATION_{suffix}", "from-irrigation-name")
    assert getattr(_settings(), field) == "from-irrigation-name"
    monkeypatch.setenv(suffix, "from-bare-name")
    assert getattr(_settings(), field) == "from-bare-name"
    monkeypatch.setenv(f"GREENHOUSE_{suffix}", "from-greenhouse-name")
    assert getattr(_settings(), field) == "from-greenhouse-name"


@pytest.mark.parametrize(("suffix", "field"), ALIASED_FIELDS, ids=[r[0] for r in ALIASED_FIELDS])
def test_aliased_fields_ignore_double_prefixed_name(clean_env, monkeypatch, suffix, field):
    monkeypatch.setenv(f"IRRIGATION_GREENHOUSE_{suffix}", "ignored")
    assert getattr(_settings(), field) is None


def test_aliased_fields_accept_both_kwarg_spellings(clean_env):
    assert _settings(mcp_token="a").mcp_token == "a"
    assert _settings(GREENHOUSE_MCP_TOKEN="b").mcp_token == "b"


# --- sources, precedence, extras -------------------------------------------


def test_dotenv_in_cwd_is_loaded_and_env_beats_it(clean_env, monkeypatch):
    (clean_env / ".env").write_text(
        "IRRIGATION_PORT=9001\nGREENHOUSE_MCP_TOKEN=dotenv-token\nIRRIGATION_CHECK_CRON_HOURS=*\nIRRIGATION_NOT_A_FIELD=1\n"
        "UNRELATED=1\n",
        encoding="utf-8",
    )
    s = Settings()
    assert (s.port, s.mcp_token, s.check_cron_hours_explicit) == (9001, "dotenv-token", True)
    assert Settings(_env_file=None).port == 8000
    monkeypatch.setenv("IRRIGATION_PORT", "9002")
    assert Settings().port == 9002
    assert Settings(port=9003).port == 9003


def test_extra_inputs_are_ignored(clean_env, monkeypatch):
    monkeypatch.setenv("IRRIGATION_NOT_A_FIELD", "1")
    s = _settings(not_a_field=1)
    assert not hasattr(s, "not_a_field")


def test_check_cron_hours_explicit_tracks_any_source(clean_env, monkeypatch):
    assert _settings().check_cron_hours_explicit is False
    assert _settings(check_cron_hours="*").check_cron_hours_explicit is True
    monkeypatch.setenv("IRRIGATION_CHECK_CRON_HOURS", "*")
    assert _settings().check_cron_hours_explicit is True


# --- exact validation messages ----------------------------------------------

VALIDATION_CASES = {
    "sync_interval=0": {"sync_interval_minutes": 0},
    "sync_interval=-1": {"sync_interval_minutes": -1},
    "cron=bogus": {"check_cron_hours": "bogus"},
    "cron=25": {"check_cron_hours": "25"},
    "cron=*/0": {"check_cron_hours": "*/0"},
    "cron=empty": {"check_cron_hours": ""},
    "cron=0,6,": {"check_cron_hours": "0,6,"},
    "legacy=5": {"check_interval_hours": 5},
    "legacy=7": {"check_interval_hours": 7},
    "legacy=24": {"check_interval_hours": 24},
    "legacy=48": {"check_interval_hours": 48},
    "legacy=0": {"check_interval_hours": 0},
    "legacy=-3": {"check_interval_hours": -3},
}


def _errors(exc: ValidationError) -> list[dict]:
    return [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in exc.errors()]


def test_startup_validation_messages_match_golden(clean_env):
    observed = {}
    for case, kwargs in VALIDATION_CASES.items():
        with pytest.raises(ValidationError) as exc:
            _settings(**kwargs)
        observed[case] = _errors(exc.value)
    assert_golden_json("contracts/settings_validation_errors.json", observed)


@pytest.mark.parametrize(
    ("env", "raw", "kwarg"),
    [
        ("IRRIGATION_SYNC_INTERVAL_MINUTES", "0", {"sync_interval_minutes": 0}),
        ("IRRIGATION_CHECK_CRON_HOURS", "bogus", {"check_cron_hours": "bogus"}),
        ("IRRIGATION_CHECK_INTERVAL_HOURS", "5", {"check_interval_hours": 5}),
    ],
)
def test_env_sourced_values_fail_with_the_same_message_as_kwargs(clean_env, monkeypatch, env, raw, kwarg):
    with pytest.raises(ValidationError) as from_kwarg:
        _settings(**kwarg)
    monkeypatch.setenv(env, raw)
    with pytest.raises(ValidationError) as from_env:
        _settings()
    assert [e["msg"] for e in from_env.value.errors()] == [e["msg"] for e in from_kwarg.value.errors()]


def test_resolve_check_cron_hours_warning_texts(clean_env, caplog):
    from greenhouse_server.scheduler import _resolve_check_cron_hours

    with caplog.at_level(logging.WARNING, logger="greenhouse_server.scheduler"):
        assert _resolve_check_cron_hours(_settings()) == "*"
        assert _resolve_check_cron_hours(_settings(check_interval_hours=6)) == "*/6"
        assert _resolve_check_cron_hours(_settings(check_cron_hours="0,12", check_interval_hours=2)) == "0,12"
    assert [(r.name, r.levelname, r.getMessage()) for r in caplog.records] == [
        (
            "greenhouse_server.scheduler",
            "WARNING",
            "IRRIGATION_CHECK_INTERVAL_HOURS is deprecated; set IRRIGATION_CHECK_CRON_HOURS instead. "
            "Translating value 6 to '*/6'.",
        ),
        (
            "greenhouse_server.scheduler",
            "WARNING",
            "Both IRRIGATION_CHECK_CRON_HOURS and the deprecated IRRIGATION_CHECK_INTERVAL_HOURS are set; "
            "using IRRIGATION_CHECK_CRON_HOURS='0,12' and ignoring the interval.",
        ),
    ]


# --- env vars read outside Settings -----------------------------------------


def _literal(node: ast.AST | None):
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


def _scan_env_reads() -> list[dict]:
    reads = []
    for path in sorted(LIBS.glob("*/greenhouse_*/**/*.py")):
        package_root = path.relative_to(LIBS).parts[0]
        module = ".".join(path.relative_to(LIBS / package_root).with_suffix("").parts)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                func = node.func
                if func.attr == "get" and _is_os_environ(func.value):
                    call = "os.environ.get"
                elif func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
                    call = "os.getenv"
                else:
                    continue
                default = node.args[1] if len(node.args) > 1 else None
                reads.append(
                    {"module": module, "call": call, "var": _literal(node.args[0]), "default": _literal(default)}
                )
            elif isinstance(node, ast.Subscript) and _is_os_environ(node.value):
                reads.append({"module": module, "call": "os.environ[]", "var": _literal(node.slice), "default": None})
    return sorted(reads, key=lambda r: (r["module"], r["var"], r["call"], repr(r["default"])))


def test_direct_env_reads_match_golden():
    """Every ``os.environ`` / ``os.getenv`` read in libs/: module, variable and default."""
    assert_golden_json("contracts/env_reads.json", _scan_env_reads())


def test_tuya_env_vars_feed_device_gateway(clean_env, monkeypatch):
    from greenhouse_core.devices import gateway as gateway_mod

    calls = []
    monkeypatch.setattr(gateway_mod.tinytuya, "Cloud", lambda **kw: calls.append(kw) or object())
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


@pytest.mark.parametrize(("value", "expect_default"), [(None, True), ("", True), ("/srv/custom.json", False)])
def test_plant_db_path_env_is_read_at_import_time(clean_env, value, expect_default):
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


def test_irrigation_tz_is_display_timezone_fallback(clean_env, monkeypatch):
    from greenhouse_core import utils

    monkeypatch.setattr(utils, "_display_timezone", None)
    monkeypatch.delenv("IRRIGATION_TZ", raising=False)
    assert utils.get_display_timezone() == "UTC"
    monkeypatch.setenv("IRRIGATION_TZ", "Europe/Rome")
    assert utils.get_display_timezone() == "Europe/Rome"
    monkeypatch.setattr(utils, "_display_timezone", "Asia/Tokyo")
    assert utils.get_display_timezone() == "Asia/Tokyo"


def test_cli_server_url_env_and_default(clean_env, monkeypatch):
    from greenhouse_cli.commands._helpers import get_client
    from greenhouse_cli.commands.auth import _login_client

    ctx = SimpleNamespace(obj=None)
    assert str(get_client(ctx).http.base_url) == "http://localhost:8000"
    assert str(_login_client(ctx).http.base_url) == "http://localhost:8000"
    monkeypatch.setenv("IRRIGATION_SERVER_URL", "http://greenhouse.invalid:9")
    assert str(get_client(ctx).http.base_url) == "http://greenhouse.invalid:9"
    assert str(_login_client(ctx).http.base_url) == "http://greenhouse.invalid:9"
    assert str(get_client(SimpleNamespace(obj="http://flag.invalid")).http.base_url) == "http://flag.invalid"


def test_cli_api_token_env_and_token_file(clean_env, monkeypatch):
    from greenhouse_cli.client import _default_token_path, load_stored_token

    assert _default_token_path() == clean_env / "config" / "greenhouse" / "token"
    assert load_stored_token() is None
    monkeypatch.setenv("GREENHOUSE_API_TOKEN", "  env-token  ")
    assert load_stored_token() == "env-token"
    monkeypatch.setenv("GREENHOUSE_API_TOKEN", "   ")
    assert load_stored_token() is None
    monkeypatch.delenv("GREENHOUSE_API_TOKEN")
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert _default_token_path() == clean_env / ".config" / "greenhouse" / "token"


def test_env_read_scan_finds_exactly_the_documented_vars():
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
