"""Irrigation config web routes: get/edit form, save."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from greenhouse_server.deps import RepoDep, require_cluster

router = APIRouter(include_in_schema=False)


_WEEKDAY_BITS: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 64)
_WEEKDAY_LABELS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_FULL_MASK = 127


def _format_weekday_mask(mask: int) -> str:
    """Render a weekday bitmask as a human label (e.g. 'Mon, Wed, Fri')."""
    if mask & _FULL_MASK == _FULL_MASK:
        return "Every day"
    return ", ".join(label for bit, label in zip(_WEEKDAY_BITS, _WEEKDAY_LABELS, strict=True) if mask & bit)


@router.get("/clusters/{cluster_id}/config")
def config_form(cluster_id: int, repo: RepoDep):
    """Legacy URL — config is now rendered inline on the unified cluster
    detail page. A 301 drops old bookmarks at the right section anchor."""
    require_cluster(repo, cluster_id)
    return RedirectResponse(url=f"/clusters/{cluster_id}#config", status_code=301)


def _parse_optional_hour(raw: str) -> int | None:
    """Form helper: empty string → None (inherit), otherwise int (validated 0..23 by caller)."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise HTTPException(400, "Invalid hour value") from exc


def _parse_tri_bool(raw: str) -> bool | None:
    """Form tri-state: ``""`` = inherit, ``"true"`` = on, ``"false"`` = off."""
    value = raw.strip().lower()
    if value == "":
        return None
    if value in ("true", "on", "1"):
        return True
    if value in ("false", "off", "0"):
        return False
    raise HTTPException(400, f"Invalid tri-bool value: {raw!r}")


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
):
    """Save the cluster's irrigation config and redirect back to detail#config.

    Empty form fields write null (inherit from global default). Quiet hours
    follow the same rule; submitting equal start/end values switches quiet
    hours off at the cluster level.
    """
    require_cluster(repo, cluster_id)
    fields: dict = {
        "mode": mode or None,
        "duration_minutes": int(duration_minutes) if duration_minutes.strip() else None,
        "interval_hours": int(interval_hours) if interval_hours.strip() else None,
        "auto_run": _parse_tri_bool(auto_run),
        "quiet_start_hour": _parse_optional_hour(quiet_start_hour),
        "quiet_end_hour": _parse_optional_hour(quiet_end_hour),
    }
    repo.set_irrigation_config(cluster_id=cluster_id, **fields)
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#config", status_code=303)
