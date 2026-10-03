"""Vacation window web routes — list, create, delete.

Form inputs accept either YYYY-MM-DD date strings or Unix timestamps (integers).
Date strings are midnight in the ``timezone`` preference — the same zone the pages
display vacation times in (``format_ts``), and the zone the TUI uses. The parsing
priority is:
  1. Try to parse as a Unix integer string (e.g. "1748476800").
  2. Fall back to parsing as ISO date "YYYY-MM-DD" (midnight in the preference timezone).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from greenhouse_core.utils import get_display_timezone
from greenhouse_server.deps import RepoDep, require_vacation_window, require_valid_vacation_range
from greenhouse_server.services.vacation import cluster_budgets
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

if TYPE_CHECKING:
    from greenhouse_core.models import VacationWindow

router = APIRouter(include_in_schema=False)


def _parse_ts(value: str) -> int:
    """Parse a Unix epoch string or YYYY-MM-DD date string into a Unix timestamp."""
    value = value.strip()
    try:
        return int(value)
    except ValueError:
        pass
    dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=_preference_zone())
    return int(dt.timestamp())


def _preference_zone() -> tzinfo:
    """The display timezone (``UserPreferences.timezone``, else ``IRRIGATION_TZ``); UTC if unknown."""
    try:
        return ZoneInfo(get_display_timezone())
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


@router.get("/vacation")
def vacation_list(request: Request, repo: RepoDep):
    """Render the vacation page with the water budget of the active or next window."""
    active = repo.get_active_vacation()
    windows = repo.list_vacation_windows()
    # Project the per-cluster water budget for the window that matters most:
    # the active one, else the next scheduled one. Only clusters with capacity
    # configured produce a readout (the helper omits the rest), matching the
    # engine's no-op behavior when no reservoir/flow is set.
    budget_window = active or _next_window(windows)
    budgets = cluster_budgets(repo, budget_window.starts_at, budget_window.ends_at) if budget_window is not None else []
    return templates.TemplateResponse(
        request,
        "vacation/list.html",
        base_context(request, active=active, windows=windows, budgets=budgets, budget_window=budget_window),
    )


def _next_window(windows: list[VacationWindow]) -> VacationWindow | None:
    """Return the soonest-starting future window, or None when none are scheduled."""
    now = int(time.time())
    upcoming = [w for w in windows if w.starts_at > now]
    if not upcoming:
        return None
    return min(upcoming, key=lambda w: w.starts_at)


@router.post("/vacation")
def create_vacation(
    request: Request,
    repo: RepoDep,
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    contact_email: str = Form(""),
    notes: str = Form(""),
):
    """Create a vacation window from the form and return to the vacation page."""
    try:
        starts_ts = _parse_ts(starts_at)
        ends_ts = _parse_ts(ends_at)
    except ValueError as exc:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD or Unix timestamp.") from exc
    require_valid_vacation_range(starts_ts, ends_ts)
    repo.add_vacation_window(
        starts_at=starts_ts,
        ends_at=ends_ts,
        contact_email=contact_email.strip() or None,
        notes=notes.strip() or None,
    )
    repo.commit()
    return RedirectResponse(url="/vacation", status_code=303)


@router.get("/vacation/{window_id}/edit")
def edit_vacation_form(request: Request, window_id: int, repo: RepoDep):
    """Render the edit form of a vacation window."""
    window = require_vacation_window(repo, window_id)
    return templates.TemplateResponse(
        request,
        "vacation/edit.html",
        base_context(request, window=window),
    )


@router.post("/vacation/{window_id}/edit")
def update_vacation(
    request: Request,
    window_id: int,
    repo: RepoDep,
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    contact_email: str = Form(""),
    notes: str = Form(""),
):
    """Save the vacation form and return to the vacation page."""
    require_vacation_window(repo, window_id)
    try:
        starts_ts = _parse_ts(starts_at)
        ends_ts = _parse_ts(ends_at)
    except ValueError as exc:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD or Unix timestamp.") from exc
    require_valid_vacation_range(starts_ts, ends_ts)
    repo.update_vacation_window(
        window_id,
        starts_at=starts_ts,
        ends_at=ends_ts,
        contact_email=contact_email.strip() or None,
        notes=notes.strip() or None,
    )
    repo.commit()
    return RedirectResponse(url="/vacation", status_code=303)


@router.post("/vacation/{window_id}/delete")
def delete_vacation(request: Request, window_id: int, repo: RepoDep):
    """Delete a vacation window and return to the vacation page."""
    require_vacation_window(repo, window_id)
    repo.delete_vacation_window(window_id)
    repo.commit()
    return RedirectResponse(url="/vacation", status_code=303)
