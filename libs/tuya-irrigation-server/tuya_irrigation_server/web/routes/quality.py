"""Data-quality report web route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from tuya_irrigation_server.deps import PlantDbDep, RepoDep
from tuya_irrigation_server.services.data_quality import build_report
from tuya_irrigation_server.web.context import base_context
from tuya_irrigation_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/quality")
def quality_page(request: Request, repo: RepoDep, plant_db: PlantDbDep):
    report = build_report(repo, plant_db)

    # Group issues by code for the template.
    grouped: dict[str, list] = {}
    for issue in report.issues:
        grouped.setdefault(issue.code, []).append(issue)

    return templates.TemplateResponse(
        request,
        "quality.html",
        base_context(request, report=report, grouped=grouped),
    )
