"""User preferences web routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from greenhouse_server.deps import RepoDep
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/preferences")
def preferences_page(request: Request, repo: RepoDep):
    prefs = repo.get_preferences()
    global_config = repo.get_global_irrigation_config()
    repo.session.commit()
    clusters = repo.list_clusters()
    return templates.TemplateResponse(
        request,
        "preferences.html",
        base_context(request, prefs=prefs, clusters=clusters, global_config=global_config),
    )


@router.post("/preferences")
def update_preferences(
    request: Request,
    repo: RepoDep,
    units: str = Form(...),
    timezone: str = Form(...),
    theme: str = Form(...),
    refresh_interval_seconds: int = Form(...),
    default_cluster_id: str = Form(""),
    dry_run_global: str = Form(""),
):
    default_cluster: int | None = None
    if default_cluster_id.strip():
        try:
            default_cluster = int(default_cluster_id)
        except ValueError:
            default_cluster = None
    repo.update_preferences(
        units=units,
        timezone=timezone,
        theme=theme,
        refresh_interval_seconds=refresh_interval_seconds,
        default_cluster_id=default_cluster,
        dry_run_global=bool(dry_run_global),
    )
    repo.session.commit()
    return RedirectResponse(url="/preferences", status_code=303)
