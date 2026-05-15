"""Irrigation config web routes: get/edit form, save."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from greenhouse_server.deps import RepoDep, require_cluster
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

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
def config_form(request: Request, cluster_id: int, repo: RepoDep):
    cluster = require_cluster(repo, cluster_id)
    config = repo.get_irrigation_config(cluster_id)
    windows = [
        {
            "id": w.id,
            "start_hour": w.start_hour,
            "end_hour": w.end_hour,
            "weekday_mask": w.weekday_mask,
            "weekday_label": _format_weekday_mask(w.weekday_mask),
            "label": w.label,
        }
        for w in repo.list_irrigation_windows(cluster_id)
    ]
    return templates.TemplateResponse(
        request,
        "configs/edit.html",
        base_context(
            request,
            cluster=cluster,
            config=config,
            windows=windows,
            weekday_bits=_WEEKDAY_BITS,
            weekday_labels=_WEEKDAY_LABELS,
        ),
    )


@router.post("/clusters/{cluster_id}/config")
def save_config(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    mode: str = Form(...),
    duration_minutes: str = Form(""),
    interval_hours: str = Form(""),
    auto_run: str = Form(""),
):
    require_cluster(repo, cluster_id)
    dm = int(duration_minutes) if duration_minutes.strip() else None
    ih = int(interval_hours) if interval_hours.strip() else None
    repo.set_irrigation_config(
        cluster_id=cluster_id,
        mode=mode,
        duration_minutes=dm,
        interval_hours=ih,
        auto_run=bool(auto_run),
    )
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}", status_code=303)
