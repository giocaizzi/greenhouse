"""Top-level full-page routes (dashboard)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from greenhouse_server.deps import RepoDep
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter()


@router.get("/", include_in_schema=False)
def dashboard(request: Request, repo: RepoDep):
    clusters = repo.list_clusters()
    return templates.TemplateResponse(request, "dashboard.html", base_context(request, clusters=clusters))
