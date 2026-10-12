"""Cluster decision-log web route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from greenhouse_server.deps import RepoDep, require_cluster
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/clusters/{cluster_id}/decisions")
def cluster_decisions(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Response:
    """Render a cluster's persisted decision log, newest first."""
    cluster = require_cluster(repo, cluster_id)
    logs = repo.list_decision_logs(cluster_id, limit=limit)
    return templates.TemplateResponse(
        request,
        "clusters/decisions.html",
        base_context(request, cluster=cluster, logs=logs, limit=limit),
    )
