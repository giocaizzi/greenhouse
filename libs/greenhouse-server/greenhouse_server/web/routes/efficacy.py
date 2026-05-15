"""Cluster irrigation-efficacy web route."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from greenhouse_server.deps import RepoDep, require_cluster
from greenhouse_server.services.efficacy import score_cluster
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/clusters/{cluster_id}/efficacy")
def cluster_efficacy_page(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    days: int = Query(default=14, ge=1, le=365),
):
    cluster = require_cluster(repo, cluster_id)
    result = score_cluster(repo, cluster_id, days=days)
    return templates.TemplateResponse(
        request,
        "clusters/efficacy.html",
        base_context(request, cluster=cluster, result=result, days=days),
    )
