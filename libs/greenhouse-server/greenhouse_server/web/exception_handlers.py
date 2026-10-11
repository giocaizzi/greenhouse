"""HX-aware exception handlers that render HTML errors for web routes.

Machine paths — ``/api/…``, ``/mcp`` and ``/.well-known/…`` — always keep JSON error
bodies. Elsewhere the client decides: an HTMX request (``HX-Request: true``) gets the
bare ``_error.html`` partial, an ``Accept`` that names ``text/html`` (a browser) gets
the full error page, an ``Accept`` that names ``application/json`` but not HTML gets
JSON, and anything else (``*/*``, no header) gets the HTML page — the web UI's default.
The handler is registered for Starlette's ``HTTPException`` so router-level 404 / 405
(unknown path, wrong method) follow the same rule as errors raised by routes.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from greenhouse_server.auth import _RedirectAuthError, render_login_redirect
from greenhouse_server.web.context import base_context, is_hx
from greenhouse_server.web.templating import templates

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

# Machine endpoints: their clients (CLI, MCP, OAuth discovery probes) must always see
# structured JSON errors, never an HTML page, whatever they send in ``Accept``.
_JSON_ONLY_PREFIXES = ("/api/", "/mcp", "/.well-known/")


def _is_html_request(request: Request) -> bool:
    """Whether to answer an error with HTML (see the module docstring for the rule)."""
    if request.url.path.startswith(_JSON_ONLY_PREFIXES):
        return False
    if is_hx(request):
        return True
    accept = request.headers.get("accept", "").lower()
    return "text/html" in accept or "application/json" not in accept


def _json_safe(value: object) -> object:
    """``value`` with every non-finite float replaced by its text (``"nan"``, ``"inf"``).

    A refused ``NaN`` / ``Infinity`` body value comes back in the 422 ``input``, and strict
    JSON cannot carry it: rendering the error must not itself fail with a 500.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _error_template(request: Request) -> str:
    # HTMX swaps fragments into existing targets, so return the bare partial.
    # Full-page errors need the base layout (nav, title, footer).
    return "_error.html" if is_hx(request) else "error_page.html"


def register_web_exception_handlers(app: FastAPI) -> None:
    """Install the HTTPException and validation handlers: HTML for browsers on web paths, JSON for API clients."""

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exc(request: Request, exc: StarletteHTTPException) -> Response:
        """Render an HTTPException (route-raised or router-level): login redirect, JSON or the HTML error page."""
        # Auth redirect sentinel — always convert to a 303 to /login, even on
        # API paths so a stale browser tab fetching /api/v1 also gets bounced
        # to the form. CLI clients should be using the bearer header, not
        # following redirects, so this only affects browsers.
        if isinstance(exc, _RedirectAuthError):
            return render_login_redirect(exc, request)
        if not _is_html_request(request):
            # Keep exc.headers: a 401 must carry ``WWW-Authenticate`` (RFC 9110 §11.6.1),
            # which AuthError and the MCP gate set and bearer clients rely on.
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        return templates.TemplateResponse(
            request,
            _error_template(request),
            base_context(request, status_code=exc.status_code, detail=exc.detail),
            status_code=exc.status_code,
            headers=exc.headers,  # e.g. ``Allow`` on a 405 — the page is still an HTTP answer
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> Response:
        """Render a request-validation error: JSON 422 for machine paths / JSON clients, else the HTML error page."""
        if not _is_html_request(request):
            return JSONResponse({"detail": _json_safe(exc.errors())}, status_code=422)
        return templates.TemplateResponse(
            request,
            _error_template(request),
            base_context(request, status_code=422, detail="Form validation failed."),
            status_code=422,
        )
