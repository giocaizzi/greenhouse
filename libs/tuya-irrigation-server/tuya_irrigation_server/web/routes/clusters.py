"""Cluster web routes: list, detail, polled status fragment."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from tuya_irrigation_server.deps import ClusterServiceDep, RepoDep
from tuya_irrigation_server.web.context import base_context
from tuya_irrigation_server.web.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/clusters")
def list_clusters(request: Request, repo: RepoDep):
    clusters = repo.list_clusters()
    return templates.TemplateResponse(request, "clusters/list.html", base_context(request, clusters=clusters))


@router.get("/clusters/{cluster_id}")
def cluster_detail(request: Request, cluster_id: int, svc: ClusterServiceDep):
    status = svc.get_cluster_status(cluster_id)
    if status is None:
        raise HTTPException(404, "Cluster not found")
    return templates.TemplateResponse(
        request,
        "clusters/detail.html",
        base_context(request, status=status, cluster_id=cluster_id),
    )


@router.get("/clusters/{cluster_id}/status-fragment")
def cluster_status_fragment(request: Request, cluster_id: int, svc: ClusterServiceDep):
    status = svc.get_cluster_status(cluster_id)
    if status is None:
        raise HTTPException(404, "Cluster not found")
    return templates.TemplateResponse(
        request,
        "partials/_cluster_status.html",
        base_context(request, status=status, cluster_id=cluster_id),
    )
