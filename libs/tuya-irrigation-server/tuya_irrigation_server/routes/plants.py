"""Plant CRUD routes."""

from fastapi import APIRouter, HTTPException, status

from tuya_irrigation_core.plant_db import get_plant_database
from tuya_irrigation_core.schemas import CreatePlantRequest, PlantResponse, SyncPlantsRequest, SyncPlantsResponse
from tuya_irrigation_server.deps import RepoDep
from tuya_irrigation_server.services.cluster import sync_plant_with_db

router = APIRouter(tags=["plants"])


@router.post("/clusters/{cluster_id}/plants", response_model=PlantResponse, status_code=status.HTTP_201_CREATED)
def add_plant(cluster_id: int, request: CreatePlantRequest, repo: RepoDep):
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
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
    repo.session.commit()
    # Re-fetch to get the committed state
    plants = repo.get_plants_in_cluster(cluster_id)
    return next(p for p in plants if p.id == plant_id)


@router.get("/clusters/{cluster_id}/plants", response_model=list[PlantResponse])
def list_plants(cluster_id: int, repo: RepoDep):
    return repo.get_plants_in_cluster(cluster_id)


@router.post("/plants/sync", response_model=SyncPlantsResponse)
def sync_plants(request: SyncPlantsRequest, repo: RepoDep):
    plant_db = get_plant_database()
    errors = []
    synced = 0

    if request.plant_id:
        # Find specific plant
        clusters = repo.list_clusters()
        plant = None
        for cluster in clusters:
            for p in repo.get_plants_in_cluster(cluster.id):
                if p.id == request.plant_id:
                    plant = p
                    break
            if plant:
                break
        if not plant:
            raise HTTPException(status_code=404, detail=f"Plant {request.plant_id} not found")
        sync_plant_with_db(repo, plant, plant_db)
        synced = 1
    else:
        clusters = [repo.get_cluster(request.cluster_id)] if request.cluster_id else repo.list_clusters()
        for cluster in clusters:
            if not cluster:
                continue
            for plant in repo.get_plants_in_cluster(cluster.id):
                try:
                    sync_plant_with_db(repo, plant, plant_db)
                    synced += 1
                except Exception as e:
                    errors.append(f"{plant.species}: {e}")

    repo.session.commit()
    return SyncPlantsResponse(synced=synced, errors=errors)
