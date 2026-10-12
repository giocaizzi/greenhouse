"""Best-effort startup and chrome reads log their failure instead of swallowing it.

``create_app`` reconfigures the root logger through Alembic, so these tests drive the
helpers directly with a stand-in app and capture the module logger with ``caplog``.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Never, cast

import pytest
from starlette.requests import Request

from greenhouse_server import app as app_module
from greenhouse_server.web import context

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI


def _broken_factory() -> Never:
    msg = "database is locked"
    raise RuntimeError(msg)


def _fake_app(factory: Callable[[], object] = _broken_factory) -> FastAPI:
    # A stand-in exposing only ``state.session_factory``: the helpers under test read nothing else.
    return cast("FastAPI", SimpleNamespace(state=SimpleNamespace(session_factory=factory)))


def test_restore_persisted_pause_logs_a_failed_read(caplog: pytest.LogCaptureFixture) -> None:
    """Restore persisted pause logs a failed read."""
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.app"):
        app_module._restore_persisted_scheduler_pause(_fake_app())  # noqa: SLF001 — helper driven directly (module docstring)
    [record] = caplog.records
    assert record.getMessage() == "Could not restore the persisted scheduler pause"
    assert record.exc_info is not None


def test_startup_timezone_logs_a_failed_read_and_falls_back_to_utc(caplog: pytest.LogCaptureFixture) -> None:
    """Startup timezone logs a failed read and falls back to UTC."""
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.app"):
        assert app_module._startup_timezone(_fake_app()) == "UTC"  # noqa: SLF001 — helper driven directly (module docstring)
    [record] = caplog.records
    assert "using UTC" in record.getMessage()
    assert record.exc_info is not None


class _BrokenRepoSession:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_preference_flags_log_a_failed_read_and_keep_defaults(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preference flags log a failed read and keep defaults."""
    session = _BrokenRepoSession()

    class _Repo:
        def get_preferences(self) -> Never:
            msg = "no such table: user_preferences"
            raise RuntimeError(msg)

    monkeypatch.setattr(context, "_repo_from_request", lambda _request: (_Repo(), session))
    request = Request({"type": "http", "headers": [], "app": _fake_app()})
    with caplog.at_level(logging.DEBUG, logger="greenhouse_server.web.context"):
        assert context._preference_flags(request) == (False, None, False, "auto", "UTC")  # noqa: SLF001 — helper driven directly (module docstring)
    [record] = caplog.records
    assert record.getMessage() == "Could not read the chrome preference flags"
    assert record.exc_info is not None
    assert session.closed


def test_repo_from_request_logs_a_failed_session_open(caplog: pytest.LogCaptureFixture) -> None:
    """Repo from request logs a failed session open."""
    request = Request({"type": "http", "headers": [], "app": _fake_app()})
    with caplog.at_level(logging.DEBUG, logger="greenhouse_server.web.context"):
        assert context._repo_from_request(request) == (None, None)  # noqa: SLF001 — helper driven directly (module docstring)
    [record] = caplog.records
    assert record.exc_info is not None


def _footer_now_text(monkeypatch: pytest.MonkeyPatch, timezone: str) -> str:
    """Render the shared template context with ``timezone`` as the stored preference; return its footer clock."""
    prefs = SimpleNamespace(dry_run_global=False, scheduler_paused=False, theme="dark", timezone=timezone)

    class _Repo:
        def get_preferences(self) -> SimpleNamespace:
            return prefs

        def get_active_vacation(self) -> None:
            return None

    monkeypatch.setattr(context, "_repo_from_request", lambda _request: (_Repo(), _BrokenRepoSession()))
    request = Request({"type": "http", "headers": [], "app": _fake_app()})
    request.app.state.settings = SimpleNamespace(auth_enabled=True)
    now_text = context.base_context(request)["now_text"]
    assert isinstance(now_text, str)
    return now_text


@pytest.mark.usefixtures("clean_env", "frozen_clock")
def test_now_text_uses_the_timezone_preference_not_the_process_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The footer clock follows ``UserPreferences.timezone`` (process ``TZ`` is UTC under ``clean_env``)."""
    assert _footer_now_text(monkeypatch, "Europe/Rome") == "2026-04-15 12:00"  # FROZEN_INSTANT 10:00 UTC, CEST


@pytest.mark.usefixtures("frozen_clock")
def test_now_text_falls_back_to_utc_for_an_unknown_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Now text falls back to UTC for an unknown zone."""
    assert _footer_now_text(monkeypatch, "Mars/Olympus_Mons") == "2026-04-15 10:00"


@pytest.mark.usefixtures("clean_env")
def test_ignored_legacy_secret_env_names_are_warned_by_name_only(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legacy secret env name (bare / IRRIGATION_) that is set is named in a WARNING; its value never is."""
    monkeypatch.setenv("MCP_TOKEN", "s3cr3t-value")
    monkeypatch.setenv("irrigation_auth_secret_key", "other-s3cr3t")
    monkeypatch.setenv("GREENHOUSE_MCP_TOKEN", "fine")
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.app"):
        app_module._warn_ignored_legacy_secret_env_names()  # noqa: SLF001 — helper driven directly (module docstring)
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "Environment variable MCP_TOKEN is set but ignored: secrets are read from GREENHOUSE_* names only",
        "Environment variable irrigation_auth_secret_key is set but ignored: "
        "secrets are read from GREENHOUSE_* names only",
    ]
    assert not any("s3cr3t" in m for m in messages)


def _app_state() -> FastAPI:
    # A stand-in with an empty ``state``: ``_init_tuya`` only writes the gateway and registry onto it.
    return cast("FastAPI", SimpleNamespace(state=SimpleNamespace()))


def test_missing_tuya_credentials_log_one_line_without_traceback(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing tuya credentials log one line without traceback."""

    def missing(**_kwargs: object) -> Never:
        msg = "Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET"
        raise ValueError(msg)

    monkeypatch.setattr(app_module, "DeviceGateway", missing)
    app = _app_state()
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.app"):
        app_module._init_tuya(app)  # noqa: SLF001 — helper driven directly (module docstring)
    [record] = caplog.records
    assert record.getMessage() == (
        "Tuya gateway not configured (Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET); starting without device control"
    )
    assert record.exc_info is None
    assert (app.state.device_gateway, app.state.device_registry) == (None, None)


def test_an_unexpected_tuya_init_error_keeps_the_traceback(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected tuya init error keeps the traceback."""

    def broken(**_kwargs: object) -> Never:
        msg = "socket exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr(app_module, "DeviceGateway", broken)
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.app"):
        app_module._init_tuya(_app_state())  # noqa: SLF001 — helper driven directly (module docstring)
    [record] = caplog.records
    assert record.exc_info is not None
