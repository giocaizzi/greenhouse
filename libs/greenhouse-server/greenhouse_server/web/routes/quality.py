"""Data-quality report web route."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import Response

from greenhouse_server.deps import PlantDbDep, RepoDep
from greenhouse_server.services.data_quality import build_report
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

if TYPE_CHECKING:
    from greenhouse_core.schemas import DataQualityIssue

router = APIRouter(include_in_schema=False)


@router.get("/quality")
def quality_page(request: Request, repo: RepoDep, plant_db: PlantDbDep) -> Response:
    """Render the data-quality report grouped by issue code."""
    report = build_report(repo, plant_db)

    # Group issues by code for the template.
    grouped: dict[str, list[DataQualityIssue]] = {}
    for issue in report.issues:
        grouped.setdefault(issue.code, []).append(issue)

    return templates.TemplateResponse(
        request,
        "quality.html",
        base_context(request, report=report, grouped=grouped),
    )
