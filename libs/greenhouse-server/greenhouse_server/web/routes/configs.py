"""Irrigation config web routes: get/edit form, save."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from greenhouse_server.deps import RepoDep, require_cluster
from greenhouse_server.web.forms import blank_or, parsed_or_400, tri_bool

router = APIRouter(include_in_schema=False)


@router.get("/clusters/{cluster_id}/config")
def config_form(cluster_id: int, repo: RepoDep) -> Response:
    """Redirect the legacy config URL to the detail page's config section (301).

    Config is rendered inline on the unified cluster detail page; the redirect
    drops old bookmarks at the right section anchor.
    """
    require_cluster(repo, cluster_id)
    return RedirectResponse(url=f"/clusters/{cluster_id}#config", status_code=301)


def _parse_optional_hour(raw: str) -> int | None:
    """Form helper: empty string → None (inherit), otherwise int (validated 0..23 by caller)."""
    return parsed_or_400(raw, int, error="Invalid hour value")


def _parse_non_negative_int(raw: str) -> int | None:
    """Form helper: empty string → None (no cap), otherwise a non-negative int."""
    return parsed_or_400(raw, int, error="Invalid numeric value", negative_error="Value must be non-negative")


@router.post("/clusters/{cluster_id}/config")
def save_config(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    mode: str = Form(""),
    duration_minutes: str = Form(""),
    interval_hours: str = Form(""),
    auto_run: str = Form(""),
    quiet_start_hour: str = Form(""),
    quiet_end_hour: str = Form(""),
) -> Response:
    """Save the cluster's irrigation config and redirect back to detail#config.

    Empty form fields write null (inherit from global default). Quiet hours
    follow the same rule; submitting equal start/end values switches quiet
    hours off at the cluster level.
    """
    require_cluster(repo, cluster_id)
    fields: dict[str, Any] = {
        "mode": mode or None,
        "duration_minutes": blank_or(duration_minutes, int),
        "interval_hours": blank_or(interval_hours, int),
        "auto_run": tri_bool(auto_run),
        "quiet_start_hour": _parse_optional_hour(quiet_start_hour),
        "quiet_end_hour": _parse_optional_hour(quiet_end_hour),
    }
    repo.set_irrigation_config(cluster_id=cluster_id, **fields)
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#config", status_code=303)


@router.post("/config/global")
def save_global_config(
    request: Request,
    repo: RepoDep,
    mode: str = Form(""),
    duration_minutes: str = Form(""),
    interval_hours: str = Form(""),
    auto_run: str = Form(""),
    daily_cap_minutes: str = Form(""),
    max_events_per_day: str = Form(""),
    quiet_start_hour: str = Form(""),
    quiet_end_hour: str = Form(""),
) -> Response:
    """Save the global irrigation defaults and redirect back to preferences.

    Empty fields write null — the effective resolver then falls through to the
    project-wide constants. Quiet hours follow the same rule; equal start/end
    values disable the global quiet window. Caps (``daily_cap_minutes`` /
    ``max_events_per_day``) write null when blank, which leaves them
    unenforced (no daily ceiling).
    """
    repo.update_global_irrigation_config(
        mode=mode or None,
        duration_minutes=blank_or(duration_minutes, int),
        interval_hours=blank_or(interval_hours, int),
        auto_run=tri_bool(auto_run),
        daily_cap_minutes=_parse_non_negative_int(daily_cap_minutes),
        max_events_per_day=_parse_non_negative_int(max_events_per_day),
        quiet_start_hour=_parse_optional_hour(quiet_start_hour),
        quiet_end_hour=_parse_optional_hour(quiet_end_hour),
    )
    repo.commit()
    return RedirectResponse(url="/preferences?saved=global#global-config", status_code=303)
