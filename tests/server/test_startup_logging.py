"""Best-effort startup and chrome reads log their failure instead of swallowing it.

``create_app`` reconfigures the root logger through Alembic, so these tests drive the
helpers directly with a stand-in app and capture the module logger with ``caplog``.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

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

    monkeypatch.setattr(context, "_repo_from_request", lambda request: (_Repo(), session))
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


def test_now_text_uses_the_timezone_preference_not_the_process_zone(clean_env, frozen_clock, monkeypatch):
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

    monkeypatch.setattr(context, "_repo_from_request", lambda request: (_Repo(), _BrokenRepoSession()))
    request = Request({"type": "http", "headers": [], "app": _fake_app()})
    request.app.state.settings = SimpleNamespace(auth_enabled=True)
    assert context.base_context(request)["now_text"] == "2026-04-15 12:00"  # FROZEN_INSTANT 10:00 UTC, CEST


def test_now_text_falls_back_to_utc_for_an_unknown_zone(frozen_clock):
    assert context._now_text("Mars/Olympus_Mons") == "2026-04-15 10:00"
