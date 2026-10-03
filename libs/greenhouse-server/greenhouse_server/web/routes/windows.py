"""Per-cluster irrigation window web routes — list, add, edit, delete.

The watering schedule section is rendered as part of the cluster config
page; this module hosts the form POST endpoints and the HTMX delete /
edit flows. Mirrors the JSON API at ``/api/v1/clusters/{id}/windows``
but uses repo methods directly (in-process, like every other web route).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from greenhouse_server.deps import RepoDep, require_cluster, require_window_in_cluster
from greenhouse_server.services.windows import WindowValidationError, validate_window
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates
from greenhouse_server.web.weekdays import WEEKDAY_BITS, WEEKDAY_LABELS

if TYPE_CHECKING:
    pass

router = APIRouter(include_in_schema=False)


def _parse_weekday_mask(values: list[str]) -> int:
    mask = 0
    for raw in values:
        try:
            bit = int(raw)
        except ValueError as exc:
            raise HTTPException(400, "weekday_mask values must be integers.") from exc
        if bit not in WEEKDAY_BITS:
            raise HTTPException(400, "weekday_mask values must be 1, 2, 4, 8, 16, 32, or 64.")
        mask |= bit
    return mask


def _validate_window_form(start_hour: int, end_hour: int, mask: int) -> None:
    """Map the shared window rule (same wording as the API) to the web's 400 page."""
    try:
        validate_window(start_hour, end_hour, mask)
    except WindowValidationError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/clusters/{cluster_id}/windows")
def create_window(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    start_hour: int = Form(...),
    end_hour: int = Form(...),
    weekday_mask: list[str] = Form(default=[]),
    label: str = Form(""),
):
    require_cluster(repo, cluster_id)
    mask = _parse_weekday_mask(weekday_mask)
    _validate_window_form(start_hour, end_hour, mask)
    repo.add_irrigation_window(
        cluster_id,
        start_hour=start_hour,
        end_hour=end_hour,
        weekday_mask=mask,
        label=label.strip() or None,
    )
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}/config", status_code=303)


@router.get("/clusters/{cluster_id}/windows/{window_id}/edit")
def edit_window_form(request: Request, cluster_id: int, window_id: int, repo: RepoDep):
    cluster = require_cluster(repo, cluster_id)
    window = require_window_in_cluster(repo, cluster_id, window_id)
    weekday_checks = [
        {"bit": bit, "label": label, "checked": bool(window.weekday_mask & bit)}
        for bit, label in zip(WEEKDAY_BITS, WEEKDAY_LABELS, strict=True)
    ]
    return templates.TemplateResponse(
        request,
        "configs/window_edit.html",
        base_context(request, cluster=cluster, window=window, weekday_checks=weekday_checks),
    )


@router.post("/clusters/{cluster_id}/windows/{window_id}/edit")
def update_window(
    request: Request,
    cluster_id: int,
    window_id: int,
    repo: RepoDep,
    start_hour: int = Form(...),
    end_hour: int = Form(...),
    weekday_mask: list[str] = Form(default=[]),
    label: str = Form(""),
):
    require_window_in_cluster(repo, cluster_id, window_id)
    mask = _parse_weekday_mask(weekday_mask)
    _validate_window_form(start_hour, end_hour, mask)
    repo.update_irrigation_window(
        window_id,
        start_hour=start_hour,
        end_hour=end_hour,
        weekday_mask=mask,
        label=label.strip() or None,
    )
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}/config", status_code=303)


@router.delete("/clusters/{cluster_id}/windows/{window_id}", response_class=HTMLResponse)
def delete_window(cluster_id: int, window_id: int, repo: RepoDep):
    """HTMX-targeted delete; returns an empty HTML body so the row is removed."""
    require_window_in_cluster(repo, cluster_id, window_id)
    repo.delete_irrigation_window(window_id)
    repo.session.commit()
    return HTMLResponse("")
