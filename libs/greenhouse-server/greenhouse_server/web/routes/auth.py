"""Web routes for the login form and logout action.

These sit outside the `require_web_user` dependency wall — a login form that
required a login would be a circular trap.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from greenhouse_core.auth import record_login
from greenhouse_server.auth import (
    _get_settings,
    _session_from_app,
    authenticate,
    clear_session_cookie,
    issue_token,
    set_session_cookie,
)
from greenhouse_server.config import Settings
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter()


def _safe_next(next_url: str | None) -> str:
    """Only follow same-host relative paths — defends against open-redirect."""
    if not next_url:
        return "/"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return "/"
    return next_url if next_url.startswith("/") else "/"


@router.get("/login", response_class=HTMLResponse)
def login_form(
    request: Request,
    next: str | None = None,
    settings: Settings = Depends(_get_settings),
) -> HTMLResponse:
    """Render the login form. ?next=… preserves the originally requested path."""
    ctx = base_context(
        request,
        next_url=_safe_next(next),
        auth_enabled=settings.auth_enabled,
        show_chrome=False,
    )
    return templates.TemplateResponse(request, "auth/login.html", ctx)


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(),
    password: str = Form(),
    next: str = Form(default="/"),
    settings: Settings = Depends(_get_settings),
    session: Session = Depends(_session_from_app),
):
    """Handle the login form. Sets a session cookie and redirects to ?next."""
    target = _safe_next(next)
    if not settings.auth_enabled:
        return RedirectResponse(url=target, status_code=303)
    user = authenticate(session, username, password)
    if user is None:
        ctx = base_context(
            request,
            next_url=target,
            error="Invalid username or password.",
            auth_enabled=settings.auth_enabled,
            show_chrome=False,
        )
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            ctx,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    token = issue_token(settings, user)
    record_login(session, user)
    session.commit()
    response = RedirectResponse(url=target, status_code=303)
    set_session_cookie(response, settings, token)
    return response


@router.post("/logout")
def logout_submit(
    settings: Settings = Depends(_get_settings),
):
    """Clear the session cookie and bounce to /login."""
    response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookie(response, settings)
    return response
