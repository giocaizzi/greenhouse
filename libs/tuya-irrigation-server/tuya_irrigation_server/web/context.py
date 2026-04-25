"""Shared template context helpers."""

from __future__ import annotations

import time

from fastapi import Request


def is_hx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def base_context(request: Request, **extra) -> dict:
    return {
        "request": request,
        "is_hx": is_hx(request),
        "now_text": time.strftime("%Y-%m-%d %H:%M"),
        **extra,
    }
