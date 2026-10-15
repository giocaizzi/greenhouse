"""Which soil band a plant runs on, and the inbox alert when it is the built-in default.

A plant whose species and category both miss the plant database is judged against
the 45-65 % fallback band, a band its pot may never reach. That must not be silent:
the plant responses carry the band and its source, and an info
``plant_care_defaulted`` alert stays open on the plant until a species or category
resolves.
"""

from typing import TypedDict

from greenhouse_core.logic.plant_needs import moisture_target_range
from greenhouse_core.models import SOURCE_PLANT, Plant
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.schemas import SoilBandSource
from greenhouse_server.services.alerts import clear_alert, raise_alert

PLANT_CARE_DEFAULTED_CODE = "plant_care_defaulted"


class PlantCare(TypedDict):
    """A plant's effective soil band and the plant-DB layer it comes from."""

    soil_moisture_min: float
    soil_moisture_max: float
    soil_band_source: SoilBandSource


def plant_care(plant_db: PlantDatabase, plant: Plant) -> PlantCare:
    """Resolve the soil band the engine, monitor and health score judge ``plant`` against."""
    care = plant_db.get_care_data(species=plant.species, category=plant.category)
    low, high = moisture_target_range(care)
    source = plant_db.soil_band_source(species=plant.species, category=plant.category)
    return {"soil_moisture_min": low, "soil_moisture_max": high, "soil_band_source": source}


def reconcile_care_alert(repo: IrrigationRepository, plant_db: PlantDatabase, plant: Plant) -> PlantCare:
    """Open (or refresh) the plant's ``plant_care_defaulted`` alert on the fallback band, else resolve it."""
    care = plant_care(plant_db, plant)
    if care["soil_band_source"] != "builtin_fallback":
        clear_alert(repo, source=SOURCE_PLANT, code=PLANT_CARE_DEFAULTED_CODE, plant_id=plant.id)
        return care
    raise_alert(
        repo,
        source=SOURCE_PLANT,
        code=PLANT_CARE_DEFAULTED_CODE,
        title=f"Default soil band · {plant.species}",
        message=(
            f"{plant.species}: neither the species nor the category ({plant.category or 'none'}) is in the "
            f"plant database, so it is judged against the default {care['soil_moisture_min']:.0f}-"
            f"{care['soil_moisture_max']:.0f}% band. Fix the species or category, or add the species."
        ),
        cluster_id=plant.cluster_id,
        plant_id=plant.id,
    )
    return care


def reconcile_cluster_care_alerts(repo: IrrigationRepository, plant_db: PlantDatabase, cluster_id: int) -> None:
    """Reconcile the ``plant_care_defaulted`` alert of every plant in the cluster (the hourly check runs it)."""
    for plant in repo.get_plants_in_cluster(cluster_id):
        reconcile_care_alert(repo, plant_db, plant)


def clear_care_alert(repo: IrrigationRepository, plant_id: int) -> None:
    """Resolve a deleted plant's alert, so a plant that later reuses the id does not inherit it."""
    clear_alert(repo, source=SOURCE_PLANT, code=PLANT_CARE_DEFAULTED_CODE, plant_id=plant_id)
