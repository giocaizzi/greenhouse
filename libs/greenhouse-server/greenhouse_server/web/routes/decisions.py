"""Cluster decision-log web route."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from greenhouse_server.deps import RepoDep, require_cluster
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/clusters/{cluster_id}/decisions")
def cluster_decisions(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    limit: int = Query(default=50, ge=1, le=200),
):
    cluster = require_cluster(repo, cluster_id)
    logs = repo.list_decision_logs(cluster_id, limit=limit)
    return templates.TemplateResponse(
        request,
        "clusters/decisions.html",
        base_context(request, cluster=cluster, logs=logs, limit=limit),
    )
