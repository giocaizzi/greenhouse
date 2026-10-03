"""Shared template context helpers."""

from __future__ import annotations

import contextlib
import time
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from fastapi import Request

from greenhouse_core.repository import IrrigationRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from greenhouse_core.models import VacationWindow


def _app_version() -> str:
    try:
        return version("greenhouse-server")
    except PackageNotFoundError:
        return "unknown"


APP_VERSION = _app_version()


def is_hx(request: Request) -> bool:
    """Return whether the request was sent by HTMX (``HX-Request: true``)."""
    return request.headers.get("HX-Request", "").lower() == "true"


def _repo_from_request(request: Request) -> tuple[IrrigationRepository, Session] | tuple[None, None]:
    """Open a private, read-only repository on ``request.app.state``, or ``(None, None)``.

    Deliberately not the request's own session: the chrome reads committed preferences
    on every render (also from exception handlers, which have no request session) and
    never writes, so the caller closes it right after the read.
    """
    try:
        factory = request.app.state.session_factory
        session = factory()
        return IrrigationRepository(session), session
    except Exception:  # noqa: BLE001
        return None, None


def _preference_flags(request: Request) -> tuple[bool, VacationWindow | None, bool, str]:
    """Read the chrome's preference flags: ``(dry_run_global, active_vacation, scheduler_paused, theme)``.

    Best effort: a failed read keeps whatever was read before it (defaults otherwise), and the
    short-lived session is always closed.
    """
    repo, session = _repo_from_request(request)
    dry_run_global = False
    active_vacation = None
    scheduler_paused = False
    # Server-rendered initial theme so a reload — or a fresh browser / second
    # device with empty localStorage — paints the persisted preference. The
    # early-apply script still prefers localStorage for instant client paint.
    theme = "auto"
    if repo is not None:
        try:
            prefs = repo.get_preferences()
            dry_run_global = prefs.dry_run_global
            scheduler_paused = prefs.scheduler_paused
            theme = prefs.theme or "auto"
            active_vacation = repo.get_active_vacation()
        except Exception:  # noqa: BLE001
            pass
        finally:
            # contract: target §3.6 keeps this close; _repo_from_request sets repo and session together.
            session.close()  # type: ignore[union-attr]
    return dry_run_global, active_vacation, scheduler_paused, theme


def _auth_enabled(request: Request) -> bool:
    """Whether auth is on; read off app.state so the topbar can hide Sign out in the no-auth dev mode."""
    auth_enabled = True
    with contextlib.suppress(AttributeError):
        auth_enabled = bool(request.app.state.settings.auth_enabled)
    return auth_enabled


def base_context(request: Request, **extra: Any) -> dict[str, Any]:
    """Build the template context every page shares (chrome flags, theme, version) merged with ``extra``."""
    dry_run_global, active_vacation, scheduler_paused, theme = _preference_flags(request)
    auth_enabled = _auth_enabled(request)

    return {
        "request": request,
        "is_hx": is_hx(request),
        "now_text": time.strftime("%Y-%m-%d %H:%M"),
        "dry_run_global": dry_run_global,
        "active_vacation": active_vacation,
        "scheduler_paused": scheduler_paused,
        "theme": theme,
        "app_version": APP_VERSION,
        "auth_enabled": auth_enabled,
        # Authenticated chrome (top nav, command palette, status pollers) is on
        # by default. Unauthenticated pages like /login pass show_chrome=False:
        # the chrome's `hx-trigger="load"` pollers would otherwise fire while
        # logged out, get redirected to /login, and recurse. See login routes.
        "show_chrome": True,
        **extra,
    }
