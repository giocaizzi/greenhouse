"""Shared template context helpers."""

from __future__ import annotations

import time
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from greenhouse_core.models import VacationWindow
    from greenhouse_core.repository import IrrigationRepository


def _app_version() -> str:
    try:
        return version("greenhouse-server")
    except PackageNotFoundError:
        return "unknown"


APP_VERSION = _app_version()


def is_hx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _repo_from_request(request: Request) -> tuple[IrrigationRepository, Session] | tuple[None, None]:
    """Resolve an IrrigationRepository from request.app.state, or None."""
    try:
        from greenhouse_core.repository import IrrigationRepository

        factory = request.app.state.session_factory
        session = factory()
        return IrrigationRepository(session), session
    except Exception:
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
        except Exception:
            pass
        finally:
            # contract: target §3.6 keeps this close; _repo_from_request sets repo and session together.
            session.close()  # type: ignore[union-attr]
    return dry_run_global, active_vacation, scheduler_paused, theme


def _auth_enabled(request: Request) -> bool:
    """Whether auth is on; read off app.state so the topbar can hide Sign out in the no-auth dev mode."""
    auth_enabled = True
    try:
        auth_enabled = bool(request.app.state.settings.auth_enabled)
    except AttributeError:
        pass
    return auth_enabled


def base_context(request: Request, **extra: Any) -> dict[str, Any]:
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
