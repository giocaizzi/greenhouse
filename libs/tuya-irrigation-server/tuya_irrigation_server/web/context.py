"""Shared template context helpers."""

from __future__ import annotations

from fastapi import Request


def is_hx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def base_context(request: Request, **extra) -> dict:
    return {"request": request, "is_hx": is_hx(request), **extra}
