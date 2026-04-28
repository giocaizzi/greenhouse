"""Shared template context helpers."""

from __future__ import annotations

import time

from fastapi import Request


def is_hx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _repo_from_request(request: Request):
    """Resolve an IrrigationRepository from request.app.state, or None."""
    try:
        from tuya_irrigation_core.repository import IrrigationRepository

        factory = request.app.state.session_factory
        session = factory()
        return IrrigationRepository(session), session
    except Exception:
        return None, None


def base_context(request: Request, **extra) -> dict:
    repo, session = _repo_from_request(request)
    dry_run_global = False
    active_vacation = None
    if repo is not None:
        try:
            dry_run_global = repo.get_preferences().dry_run_global
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
        **extra,
    }
