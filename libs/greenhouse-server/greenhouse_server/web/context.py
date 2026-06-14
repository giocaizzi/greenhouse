"""Shared template context helpers."""

from __future__ import annotations

import time
from importlib.metadata import PackageNotFoundError, version

from fastapi import Request


def _app_version() -> str:
    try:
        return version("greenhouse-server")
    except PackageNotFoundError:
        return "unknown"


APP_VERSION = _app_version()


def is_hx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _repo_from_request(request: Request):
    """Resolve an IrrigationRepository from request.app.state, or None."""
    try:
        from greenhouse_core.repository import IrrigationRepository

        factory = request.app.state.session_factory
        session = factory()
        return IrrigationRepository(session), session
    except Exception:
        return None, None


def base_context(request: Request, **extra) -> dict:
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
            session.close()

    # auth_enabled is read off app.state so the topbar can hide the Sign out
    # button when running in the no-auth dev mode.
    auth_enabled = True
    try:
        auth_enabled = bool(request.app.state.settings.auth_enabled)
    except AttributeError:
        pass

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
