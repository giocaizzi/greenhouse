"""Per-cluster irrigation window web routes — list, add, edit, delete.

The watering schedule is rendered in the config section of the cluster
detail page; this module hosts the form POST endpoints and the HTMX delete /
edit flows. Mirrors the JSON API at ``/api/v1/clusters/{id}/windows`` and
shares its validator (``services.windows.validate_window``), calling the
repository in-process like every other web route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from greenhouse_server.deps import RepoDep, require_cluster, require_valid_window, require_window_in_cluster
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates
from greenhouse_server.web.weekdays import WEEKDAY_BITS, WEEKDAY_LABELS

router = APIRouter(include_in_schema=False)


def _parse_weekday_mask(values: list[str]) -> int:
    mask = 0
    for raw in values:
        try:
            bit = int(raw)
        except ValueError as exc:
            raise HTTPException(400, "weekday_mask values must be integers") from exc
        if bit not in WEEKDAY_BITS:
            raise HTTPException(400, "weekday_mask values must be 1, 2, 4, 8, 16, 32, or 64")
        mask |= bit
    return mask


@dataclass(frozen=True, slots=True)
class _NewWindowForm:
    """``POST /clusters/{cluster_id}/windows``: hours, ticked weekday checkboxes (bit values), label."""

    cluster_id: Annotated[int, Path()]
    start_hour: Annotated[int, Form()]
    end_hour: Annotated[int, Form()]
    weekday_mask: Annotated[list[str], Form(default_factory=list)]
    label: Annotated[str, Form()] = ""


@dataclass(frozen=True, slots=True)
class _EditWindowForm:
    """``POST /clusters/{cluster_id}/windows/{window_id}/edit``: the same fields for an existing window."""

    cluster_id: Annotated[int, Path()]
    window_id: Annotated[int, Path()]
    start_hour: Annotated[int, Form()]
    end_hour: Annotated[int, Form()]
    weekday_mask: Annotated[list[str], Form(default_factory=list)]
    label: Annotated[str, Form()] = ""


@router.post("/clusters/{cluster_id}/windows")
def create_window(repo: RepoDep, form: Annotated[_NewWindowForm, Depends()]) -> Response:
    """Add an irrigation window from the form and return to the cluster config."""
    require_cluster(repo, form.cluster_id)
    mask = _parse_weekday_mask(form.weekday_mask)
    require_valid_window(form.start_hour, form.end_hour, mask)
    repo.add_irrigation_window(
        form.cluster_id,
        start_hour=form.start_hour,
        end_hour=form.end_hour,
        weekday_mask=mask,
        label=form.label.strip() or None,
    )
    repo.commit()
    return RedirectResponse(url=f"/clusters/{form.cluster_id}/config", status_code=303)


@router.get("/clusters/{cluster_id}/windows/{window_id}/edit")
def edit_window_form(request: Request, cluster_id: int, window_id: int, repo: RepoDep) -> Response:
    """Render the edit form of one of the cluster's irrigation windows."""
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
def update_window(repo: RepoDep, form: Annotated[_EditWindowForm, Depends()]) -> Response:
    """Save the window form and return to the cluster config."""
    require_window_in_cluster(repo, form.cluster_id, form.window_id)
    mask = _parse_weekday_mask(form.weekday_mask)
    require_valid_window(form.start_hour, form.end_hour, mask)
    repo.update_irrigation_window(
        form.window_id,
        start_hour=form.start_hour,
        end_hour=form.end_hour,
        weekday_mask=mask,
        label=form.label.strip() or None,
    )
    repo.commit()
    return RedirectResponse(url=f"/clusters/{form.cluster_id}/config", status_code=303)


@router.delete("/clusters/{cluster_id}/windows/{window_id}", response_class=HTMLResponse)
def delete_window(cluster_id: int, window_id: int, repo: RepoDep) -> Response:
    """HTMX-targeted delete; returns an empty HTML body so the row is removed."""
    require_window_in_cluster(repo, cluster_id, window_id)
    repo.delete_irrigation_window(window_id)
    repo.commit()
    return HTMLResponse("")
