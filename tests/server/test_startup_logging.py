"""Best-effort startup and chrome reads log their failure instead of swallowing it.

``create_app`` reconfigures the root logger through Alembic, so these tests drive the
helpers directly with a stand-in app and capture the module logger with ``caplog``.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from greenhouse_server import app as app_module
from greenhouse_server.web import context


def _broken_factory():
    msg = "database is locked"
    raise RuntimeError(msg)


def _fake_app(factory=_broken_factory):
    return SimpleNamespace(state=SimpleNamespace(session_factory=factory))


def test_restore_persisted_pause_logs_a_failed_read(caplog):
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.app"):
        app_module._restore_persisted_scheduler_pause(_fake_app())
    [record] = caplog.records
    assert record.getMessage() == "Could not restore the persisted scheduler pause"
    assert record.exc_info is not None


def test_startup_timezone_logs_a_failed_read_and_falls_back_to_utc(caplog):
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.app"):
        assert app_module._startup_timezone(_fake_app()) == "UTC"
    [record] = caplog.records
    assert "using UTC" in record.getMessage()
    assert record.exc_info is not None


class _BrokenRepoSession:
    closed = False

    def close(self):
        self.closed = True


def test_preference_flags_log_a_failed_read_and_keep_defaults(caplog, monkeypatch):
    session = _BrokenRepoSession()

    class _Repo:
        def get_preferences(self):
            msg = "no such table: user_preferences"
            raise RuntimeError(msg)

    monkeypatch.setattr(context, "_repo_from_request", lambda _request: (_Repo(), session))
    request = Request({"type": "http", "headers": [], "app": _fake_app()})
    with caplog.at_level(logging.DEBUG, logger="greenhouse_server.web.context"):
        assert context._preference_flags(request) == (False, None, False, "auto", "UTC")
    [record] = caplog.records
    assert record.getMessage() == "Could not read the chrome preference flags"
    assert record.exc_info is not None
    assert session.closed


def test_repo_from_request_logs_a_failed_session_open(caplog):
    request = Request({"type": "http", "headers": [], "app": _fake_app()})
    with caplog.at_level(logging.DEBUG, logger="greenhouse_server.web.context"):
        assert context._repo_from_request(request) == (None, None)
    [record] = caplog.records
    assert record.exc_info is not None


@pytest.mark.usefixtures("clean_env", "frozen_clock")
def test_now_text_uses_the_timezone_preference_not_the_process_zone(monkeypatch):
    """The footer clock follows ``UserPreferences.timezone`` (process ``TZ`` is UTC under ``clean_env``)."""

    class _Prefs:
        dry_run_global = False
        scheduler_paused = False
        theme = "dark"
        timezone = "Europe/Rome"

    class _Repo:
        def get_preferences(self):
            return _Prefs()

        def get_active_vacation(self):
            return None

    monkeypatch.setattr(context, "_repo_from_request", lambda _request: (_Repo(), _BrokenRepoSession()))
    request = Request({"type": "http", "headers": [], "app": _fake_app()})
    request.app.state.settings = SimpleNamespace(auth_enabled=True)
    assert context.base_context(request)["now_text"] == "2026-04-15 12:00"  # FROZEN_INSTANT 10:00 UTC, CEST


@pytest.mark.usefixtures("frozen_clock")
def test_now_text_falls_back_to_utc_for_an_unknown_zone():
    assert context._now_text("Mars/Olympus_Mons") == "2026-04-15 10:00"


@pytest.mark.usefixtures("clean_env")
def test_ignored_legacy_secret_env_names_are_warned_by_name_only(caplog, monkeypatch):
    """A legacy secret env name (bare / IRRIGATION_) that is set is named in a WARNING; its value never is."""
    monkeypatch.setenv("MCP_TOKEN", "s3cr3t-value")
    monkeypatch.setenv("irrigation_auth_secret_key", "other-s3cr3t")
    monkeypatch.setenv("GREENHOUSE_MCP_TOKEN", "fine")
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.app"):
        app_module._warn_ignored_legacy_secret_env_names()
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "Environment variable MCP_TOKEN is set but ignored: secrets are read from GREENHOUSE_* names only",
        "Environment variable irrigation_auth_secret_key is set but ignored: "
        "secrets are read from GREENHOUSE_* names only",
    ]
    assert not any("s3cr3t" in m for m in messages)


def _app_state():
    return SimpleNamespace(state=SimpleNamespace())


def test_missing_tuya_credentials_log_one_line_without_traceback(caplog, monkeypatch):
    def missing():
        msg = "Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET"
        raise ValueError(msg)

    monkeypatch.setattr(app_module, "DeviceGateway", missing)
    app = _app_state()
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.app"):
        app_module._init_tuya(app)
    [record] = caplog.records
    assert record.getMessage() == (
        "Tuya gateway not configured (Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET); starting without device control"
    )
    assert record.exc_info is None
    assert (app.state.device_gateway, app.state.device_registry) == (None, None)


def test_an_unexpected_tuya_init_error_keeps_the_traceback(caplog, monkeypatch):
    def broken():
        msg = "socket exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr(app_module, "DeviceGateway", broken)
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.app"):
        app_module._init_tuya(_app_state())
    [record] = caplog.records
    assert record.exc_info is not None
