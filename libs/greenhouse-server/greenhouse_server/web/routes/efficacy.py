"""Cluster irrigation-efficacy web route."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from greenhouse_server.deps import RepoDep, require_cluster
from greenhouse_server.services.efficacy import EFFICACY_DEFAULT_DAYS, EFFICACY_MAX_DAYS, score_cluster
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/clusters/{cluster_id}/efficacy")
def cluster_efficacy_page(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    days: int = Query(default=EFFICACY_DEFAULT_DAYS, ge=1, le=EFFICACY_MAX_DAYS),
):
    """Render a cluster's per-event irrigation efficacy scores."""
    cluster = require_cluster(repo, cluster_id)
    result = score_cluster(repo, cluster_id, days=days)
    return templates.TemplateResponse(
        request,
        "clusters/efficacy.html",
        base_context(request, cluster=cluster, result=result, days=days),
    )
