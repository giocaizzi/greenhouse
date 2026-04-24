"""HX-aware exception handler that renders HTML errors for web routes.

API routes keep their default JSON error responses — we only intervene when
the request looks like a browser/HTMX call (Accept: text/html or HX-Request:
true) AND is not under /api/v1.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError

from tuya_irrigation_server.web.context import base_context
from tuya_irrigation_server.web.templating import templates


def _is_html_request(request: Request) -> bool:
    # API routes keep JSON error shape; everything else is rendered as HTML.
    return not request.url.path.startswith("/api/")


def register_web_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def handle_http_exc(request: Request, exc: HTTPException):
        if not _is_html_request(request):
            # Preserve the API JSON error shape.
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(
            request,
            "_error.html",
            base_context(request, status_code=exc.status_code, detail=exc.detail),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError):
        if not _is_html_request(request):
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": exc.errors()}, status_code=422)
        return templates.TemplateResponse(
            request,
            "_error.html",
            base_context(request, status_code=422, detail="Form validation failed."),
            status_code=422,
        )
