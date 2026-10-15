"""Plant CRUD routes."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from greenhouse_core.models import Plant
from greenhouse_core.repository import NotArchivedError, SameClusterMoveError
from greenhouse_core.schemas import (
    CreatePlantRequest,
    MovePlantRequest,
    PlantDetailResponse,
    PlantHealthResponse,
    PlantListResponse,
    PlantResponse,
    SuccessResponse,
    SyncPlantsRequest,
    SyncPlantsResponse,
    UpdatePlantRequest,
)
from greenhouse_server.deps import (
    ClusterServiceDep,
    PlantDbDep,
    PlantHealthServiceDep,
    RepoDep,
    require_cluster,
    require_plant,
    require_plant_in_cluster,
)
from greenhouse_server.services.errors import ClusterNotFoundError, PlantNotFoundError
from greenhouse_server.services.plant_care import clear_care_alert, reconcile_care_alert

router = APIRouter(tags=["plants"])


@router.get("/plants", response_model=PlantListResponse, summary="List plants across all clusters")
def list_all_plants(  # noqa: PLR0913 — one parameter per query filter: FastAPI reads them from the signature
    repo: RepoDep,
    cluster_id: Annotated[int | None, Query(description="Restrict results to a specific cluster")] = None,
    category: Annotated[str | None, Query(description="Restrict results to a plant category")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[int | None, Query(description="Id cursor — return rows with id > cursor")] = None,
    *,
    include_archived: Annotated[bool, Query(description="Also list archived (soft-deleted) rows")] = False,
) -> PlantListResponse:
    """List every plant across all clusters, with optional filters and cursor pagination.

    The cluster-scoped list at ``/clusters/{id}/plants`` is unchanged; this
    endpoint exists so MCP tools, dashboards, and the global search can walk
    the full plant collection without enumerating clusters first.

    Args:
        cluster_id: Restrict to a single cluster.
        category: Restrict to plants whose ``category`` matches exactly.
        limit: Page size (default 100, max 500).
        cursor: Id-based cursor — pass the previous response's ``next_cursor``
            to fetch the next page.
        include_archived: Also list archived plants (left out by default).

    Returns:
        The page of plants plus a ``next_cursor`` (None when the page was not
        full and there are no more rows to fetch).
    """
    rows = repo.list_all_plants(
        filter_cluster_id=cluster_id,
        filter_category=category,
        limit=limit,
        after_id=cursor,
        include_archived=include_archived,
    )
    next_cursor = rows[-1].id if len(rows) == limit else None
    return PlantListResponse(
        plants=[PlantResponse.model_validate(p) for p in rows],
        next_cursor=next_cursor,
    )


def _reconciled_detail(repo: RepoDep, plant_db: PlantDbDep, plant: Plant) -> PlantDetailResponse:
    """Reconcile the plant's default-band alert (the caller commits) and build its detail response."""
    care = reconcile_care_alert(repo, plant_db, plant)
    return PlantDetailResponse.model_validate({**PlantResponse.model_validate(plant).model_dump(), **care})


@router.post("/clusters/{cluster_id}/plants", response_model=PlantDetailResponse, status_code=status.HTTP_201_CREATED)
def add_plant(cluster_id: int, request: CreatePlantRequest, repo: RepoDep, plant_db: PlantDbDep) -> PlantDetailResponse:
    """Add a plant to a cluster.

    Care thresholds (water needs, temperature/humidity ranges) can be supplied
    directly or left null to be filled in later via POST /plants/sync, which
    looks them up in the evidence-based plant database. The response names the
    soil band the plant is judged against and its source; when neither species
    nor category is in the plant database (``soil_band_source`` =
    ``builtin_fallback``) an info ``plant_care_defaulted`` alert is opened.

    Args:
        cluster_id: ID of the cluster the plant belongs to.
        request: Species and optional plant-care fields.

    Raises:
        HTTPException: 404 if the cluster does not exist.

    Returns:
        The created plant including its ID, parent cluster and effective soil band.
    """
    require_cluster(repo, cluster_id)
    plant_id = repo.add_plant(
        cluster_id=cluster_id,
        species=request.species,
        category=request.category,
        water_needs=request.water_needs,
        light_needs=request.light_needs,
        ideal_temp_min=request.ideal_temp_min,
        ideal_temp_max=request.ideal_temp_max,
        ideal_humidity_min=request.ideal_humidity_min,
        ideal_humidity_max=request.ideal_humidity_max,
        notes=request.notes,
    )
    plants = repo.get_plants_in_cluster(cluster_id)
    detail = _reconciled_detail(repo, plant_db, next(p for p in plants if p.id == plant_id))
    repo.commit()
    return detail


@router.get("/clusters/{cluster_id}/plants", response_model=list[PlantResponse])
def list_plants(
    cluster_id: int,
    repo: RepoDep,
    *,
    include_archived: Annotated[bool, Query(description="Also list archived (soft-deleted) rows")] = False,
) -> list[Plant]:
    """List every plant in a cluster.

    Args:
        cluster_id: ID of the cluster to enumerate.
        include_archived: Also list archived plants (left out by default).

    Returns:
        The cluster's plants (empty for an unknown cluster).
    """
    return repo.get_plants_in_cluster(cluster_id, include_archived=include_archived)


@router.put("/clusters/{cluster_id}/plants/{plant_id}", response_model=PlantDetailResponse, summary="Update a plant")
def update_plant(
    cluster_id: int, plant_id: int, request: UpdatePlantRequest, repo: RepoDep, plant_db: PlantDbDep
) -> PlantDetailResponse:
    """Partially update a plant's care metadata.

    Only fields present in the request body are modified; omitted fields are
    left unchanged. An explicit `null` clears an optional field and is
    ignored for a required one. The plant must belong to the specified cluster.
    A species or category that now resolves in the plant database resolves the
    plant's ``plant_care_defaulted`` alert; one that no longer does opens it.

    Args:
        cluster_id: Cluster the plant belongs to.
        plant_id: Numeric plant identifier.
        request: Fields to update — any subset of care metadata fields.

    Returns:
        The updated plant with its effective soil band and its source.

    Raises:
        HTTPException: 404 if the plant does not exist or belongs to a
            different cluster.
    """
    plant = require_plant_in_cluster(repo, cluster_id, plant_id)
    repo.update_plant(plant_id, **request.model_dump(exclude_unset=True))
    detail = _reconciled_detail(repo, plant_db, plant)
    repo.commit()
    return detail


@router.delete(
    "/clusters/{cluster_id}/plants/{plant_id}", response_model=SuccessResponse, summary="Archive (or purge) a plant"
)
def delete_plant(
    cluster_id: int,
    plant_id: int,
    repo: RepoDep,
    *,
    purge: Annotated[
        bool, Query(description="Hard-delete instead of archiving; only allowed on an already archived row")
    ] = False,
) -> SuccessResponse:
    """Archive a plant (soft delete), or purge an already archived one.

    Archiving keeps the plant's id, health history and attributed readings, and
    hides it from listings, health snapshots and decisions; its sensors stay in
    the cluster, unlinked. `POST .../restore` undoes it. `purge=true`
    hard-deletes an archived plant (its health snapshots go too); this is
    irreversible. Both write an activity event naming the plant.

    Args:
        cluster_id: Cluster the plant belongs to.
        plant_id: Numeric plant identifier.
        purge: Hard-delete instead of archiving (the plant must be archived).

    Returns:
        success=True once archived (idempotent) or purged.

    Raises:
        HTTPException: 404 if the plant does not exist or belongs to a
            different cluster; 409 on `purge=true` for a plant that is not archived.
    """
    require_plant_in_cluster(repo, cluster_id, plant_id)
    if purge:
        try:
            repo.purge_plant(plant_id)
        except NotArchivedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
    else:
        repo.archive_plant(plant_id)
    clear_care_alert(repo, plant_id)
    repo.commit()
    return SuccessResponse(success=True)


@router.post(
    "/clusters/{cluster_id}/plants/{plant_id}/restore",
    response_model=PlantResponse,
    summary="Restore an archived plant",
)
def restore_plant(cluster_id: int, plant_id: int, repo: RepoDep) -> Plant | None:
    """Bring an archived plant back, with the same id and history (no-op for an active plant).

    Its former sensors are not re-linked: move them back with the sensor move tool.

    Args:
        cluster_id: Cluster the plant belongs to.
        plant_id: Numeric plant identifier.

    Returns:
        The restored plant.

    Raises:
        HTTPException: 404 if the plant does not exist or belongs to a
            different cluster.
    """
    require_plant_in_cluster(repo, cluster_id, plant_id)
    plant = repo.restore_plant(plant_id)
    repo.commit()
    return plant


@router.post("/plants/{plant_id}/move", response_model=PlantResponse)
def move_plant(plant_id: int, request: MovePlantRequest, repo: RepoDep) -> Plant | None:
    """Move a plant from its current cluster to a different cluster.

    The plant keeps its id, its plant_health_daily history, and its learning
    profile because those key off plant_id. Sensors linked to the plant are
    reassigned to the target cluster so their readings travel with the plant
    — the soil probes are physically stuck in the plant's pot. Decision logs,
    irrigation events, and alerts stay attached to the original cluster so
    the audit trail reflects where the plant actually was at the time. An
    activity event of type ``plant_moved`` is recorded with the before/after
    cluster ids and the ids of the sensors that moved with the plant.

    Args:
        plant_id: Database id of the plant to move.
        request: Target cluster the plant should belong to after the move.

    Returns:
        The updated plant with its new cluster_id.

    Raises:
        HTTPException: 404 if the plant or the target cluster does not exist,
            400 if ``target_cluster_id`` equals the plant's current cluster
            (no-op move).
    """
    require_plant(repo, plant_id)
    if not repo.get_cluster(request.target_cluster_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target cluster not found")
    try:
        moved = repo.move_plant(plant_id, request.target_cluster_id)
    except SameClusterMoveError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    repo.commit()
    return moved


@router.post("/plants/sync", response_model=SyncPlantsResponse)
def sync_plants(request: SyncPlantsRequest, repo: RepoDep, cluster_svc: ClusterServiceDep) -> SyncPlantsResponse:
    """Refresh plant care thresholds from the evidence-based plant database.

    Resolves species to care data lookup and writes the result onto the matching
    plant rows. Use this after editing the bundled plant_database.json or after
    adding plants without explicit care fields. Scope is widening: single plant
    to cluster to entire database, depending on which field is set.

    Args:
        request: One of plant_id (single plant), cluster_id (all plants in
            that cluster), or neither (every plant in every cluster).

    Returns:
        Count of plants successfully synced and a list of error messages for
        any that failed (failures do not abort the rest of the run).

    Raises:
        HTTPException: 404 if plant_id is set and no such plant exists, or if
            only cluster_id is set and no such cluster exists.
    """
    try:
        synced, errors = cluster_svc.sync_plants(plant_id=request.plant_id, cluster_id=request.cluster_id)
    except PlantNotFoundError:
        raise HTTPException(status_code=404, detail=f"Plant {request.plant_id} not found") from None
    except ClusterNotFoundError:
        raise HTTPException(status_code=404, detail="Cluster not found") from None

    repo.commit()
    return SyncPlantsResponse(synced=synced, errors=errors)


class SnapshotResponse(BaseModel):
    """Result of a manual health-snapshot run."""

    rows_written: int


@router.get("/plants/{plant_id}/health", response_model=PlantHealthResponse)
def get_plant_health(plant_id: int, repo: RepoDep, health_svc: PlantHealthServiceDep) -> PlantHealthResponse:
    """Return the current health score and 90-day history for a plant.

    Computes a fresh 0-100 composite score from readings over the last 14 days
    (soil/temp/humidity in-band fractions + irrigation efficiency) and returns
    the stored daily snapshots for charting.

    Args:
        plant_id: Database ID of the plant.

    Returns:
        Current score (None when no data), per-component breakdowns, and the
        list of daily snapshots ordered oldest-first.

    Raises:
        HTTPException: 404 if the plant does not exist.
    """
    plant = require_plant(repo, plant_id)
    result = health_svc.compute_score(plant_id)
    history = repo.list_plant_health_history(plant_id, days=90)
    return PlantHealthResponse.model_validate(
        {"plant_id": plant_id, "species": plant.species, "current_score": result["score"], "history": history}
    )


@router.post("/plants/health/snapshot", response_model=SnapshotResponse)
def trigger_health_snapshot(health_svc: PlantHealthServiceDep, repo: RepoDep) -> SnapshotResponse:
    """Compute today's health score for every plant and persist the daily snapshots.

    Intended for manual ops and testing. The scheduler calls this automatically
    at 00:30 UTC every day.

    Returns:
        Number of plant rows written (plants with no data are skipped).
    """
    rows = health_svc.snapshot_daily()
    repo.commit()
    return SnapshotResponse(rows_written=rows)
