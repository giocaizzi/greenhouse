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
    if repo is not None:
        try:
            prefs = repo.get_preferences()
            dry_run_global = prefs.dry_run_global
            scheduler_paused = prefs.scheduler_paused
            active_vacation = repo.get_active_vacation()
        except Exception:
            pass
        finally:
            session.close()

    return {
        "request": request,
        "is_hx": is_hx(request),
        "now_text": time.strftime("%Y-%m-%d %H:%M"),
        "dry_run_global": dry_run_global,
        "active_vacation": active_vacation,
        "scheduler_paused": scheduler_paused,
        "app_version": APP_VERSION,
        **extra,
    }
