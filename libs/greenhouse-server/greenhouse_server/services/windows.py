"""Irrigation-window rules shared by the JSON API and the web UI."""

from __future__ import annotations

from greenhouse_core.constants import FULL_WEEKDAY_MASK, WINDOW_HOUR_MAX


class WindowValidationError(ValueError):
    """An irrigation window's hours or weekday mask are invalid; ``str(exc)`` is the user-facing message."""


def validate_window(start_hour: int, end_hour: int, weekday_mask: int) -> None:
    """Check a window's effective hours and weekday mask, in that order.

    One rule (and one wording) for every write path: API create/update and the web
    forms. Each caller maps the error to its own HTTP 400.

    Raises:
        WindowValidationError: an hour is outside ``0..23``, the hours are equal, or
            the mask selects no weekday / unknown bits.
    """
    if not (0 <= start_hour <= WINDOW_HOUR_MAX and 0 <= end_hour <= WINDOW_HOUR_MAX):
        raise WindowValidationError("start_hour and end_hour must be 0..23")
    if start_hour == end_hour:
        raise WindowValidationError("start_hour and end_hour must differ")
    if not (1 <= weekday_mask <= FULL_WEEKDAY_MASK):
        raise WindowValidationError("weekday_mask must be 1..127 (Mon=1, Sun=64)")
