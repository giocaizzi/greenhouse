"""Irrigation config routes (declared + global defaults + effective view)."""

from fastapi import APIRouter, HTTPException

from greenhouse_core.schemas import (
    ConfigResponse,
    EffectiveConfigResponse,
    GlobalConfigResponse,
    ResolvedConfigField,
    SetConfigRequest,
    UpdateGlobalConfigRequest,
)
from greenhouse_server.deps import RepoDep, require_cluster

router = APIRouter(tags=["configs"])


def _request_fields(request: SetConfigRequest | UpdateGlobalConfigRequest) -> dict:
    """Pull only fields the client explicitly set — preserving null as a
    deliberate "clear this override" signal — and drop omitted ones so the
    repository can patch without touching unrelated columns.
    """
    return request.model_dump(exclude_unset=True)


@router.put("/clusters/{cluster_id}/config", response_model=ConfigResponse)
def set_config(cluster_id: int, request: SetConfigRequest, repo: RepoDep):
    """Patch a cluster's irrigation config.

    Every field is optional and nullable: omit a field to leave it unchanged,
    set it to a value to override the global default, or set it to ``null`` to
    clear an existing override and re-inherit. Quiet hours follow the same
    pattern; passing ``quiet_start_hour == quiet_end_hour`` switches quiet
    hours off at the cluster level (useful for outdoor clusters that should
    ignore an inherited indoor-friendly window).

    Args:
        cluster_id: Cluster to configure.
        request: Partial config payload — only the fields the client sets are
            applied.

    Returns:
        The declared (raw) config row after the patch.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    require_cluster(repo, cluster_id)
    repo.set_irrigation_config(cluster_id=cluster_id, **_request_fields(request))
    repo.session.commit()
    return repo.get_irrigation_config(cluster_id)


@router.get("/clusters/{cluster_id}/config", response_model=ConfigResponse)
def get_config(cluster_id: int, repo: RepoDep):
    """Read a cluster's declared (raw) irrigation config.

    Nulls represent inherited values — call ``GET .../config/effective`` for
    the merged view used by the decision engine.

    Args:
        cluster_id: Cluster to inspect.

    Raises:
        HTTPException: 404 if the cluster has no config row yet.
    """
    config = repo.get_irrigation_config(cluster_id)
    if not config:
        raise HTTPException(status_code=404, detail="No config set for cluster")
    return config


@router.get(
    "/clusters/{cluster_id}/config/effective",
    response_model=EffectiveConfigResponse,
)
def get_effective_config(cluster_id: int, repo: RepoDep):
    """Read a cluster's effective irrigation config (resolved across levels).

    Walks cluster → global defaults → built-in constants and reports both
    the resolved value and the source level per field, so the UI can render
    inheritance state (``override`` vs ``↳ default``) without re-querying.

    Args:
        cluster_id: Cluster to inspect.

    Returns:
        Declared per-cluster row plus a ``effective`` map of
        ``field → {value, source}`` covering every configurable field.

    Raises:
        HTTPException: 404 if the cluster does not exist.
    """
    require_cluster(repo, cluster_id)
    declared = repo.get_irrigation_config(cluster_id)
    effective = repo.get_effective_config(cluster_id)
    return EffectiveConfigResponse(
        cluster_id=cluster_id,
        declared=ConfigResponse.model_validate(declared) if declared else None,
        effective={key: ResolvedConfigField(**val) for key, val in effective.items()},
    )


@router.get("/config/global", response_model=GlobalConfigResponse)
def get_global_config(repo: RepoDep):
    """Read the singleton global irrigation defaults row.

    Returns:
        The current global defaults; nulls mean "fall through to the
        project-wide constant in :mod:`greenhouse_core.constants`."
    """
    return repo.get_global_irrigation_config()


@router.put("/config/global", response_model=GlobalConfigResponse)
def update_global_config(request: UpdateGlobalConfigRequest, repo: RepoDep):
    """Patch the singleton global irrigation defaults.

    Omitted fields stay unchanged; pass ``null`` to clear a previously set
    default so the project-wide constant takes over. Quiet hours are part of
    this surface — change ``quiet_start_hour`` / ``quiet_end_hour`` here to
    move the system-wide window; set both to the same value to disable quiet
    hours globally (per-cluster overrides remain in effect).

    Returns:
        The updated global defaults row.
    """
    repo.update_global_irrigation_config(**_request_fields(request))
    repo.session.commit()
    return repo.get_global_irrigation_config()
