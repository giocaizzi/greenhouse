"""HTML form-field parsing shared by the web routes.

HTML forms post every field as a string, and a blank field means "not set" (inherit, no cap,
no plant). These helpers spell that rule once. Each caller keeps its own 400 message, so the
pages render exactly the errors they always did.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from fastapi import HTTPException

if TYPE_CHECKING:
    from collections.abc import Callable

_Number = TypeVar("_Number", int, float)


def blank_or(raw: str | None, parse: Callable[[str], _Number]) -> _Number | None:
    """``None`` for a missing or blank field, else ``parse(raw)``.

    A ``ValueError`` from ``parse`` propagates unchanged; use :func:`parsed_or_400` to turn it
    into a 400.
    """
    if raw is None or not raw.strip():
        return None
    return parse(raw)


def parsed_or_400(
    raw: str, parse: Callable[[str], _Number], *, error: str, negative_error: str | None = None
) -> _Number | None:
    """Like :func:`blank_or`, but a malformed value is HTTP 400 ``error``.

    With ``negative_error`` set, a value below zero is HTTP 400 ``negative_error``.
    """
    try:
        value = blank_or(raw, parse)
    except ValueError as exc:
        raise HTTPException(400, error) from exc
    if negative_error is not None and value is not None and value < 0:
        raise HTTPException(400, negative_error)
    return value


def tri_bool(raw: str) -> bool | None:
    """Form tri-state: ``""`` = inherit, ``"true"`` / ``"on"`` / ``"1"`` = on, ``"false"`` / ``"off"`` / ``"0"`` = off."""
    value = raw.strip().lower()
    if value == "":
        return None
    if value in ("true", "on", "1"):
        return True
    if value in ("false", "off", "0"):
        return False
    raise HTTPException(400, f"Invalid tri-bool value: {raw!r}")
