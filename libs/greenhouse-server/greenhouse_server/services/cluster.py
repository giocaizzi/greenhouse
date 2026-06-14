"""Cluster status and history services."""

import time

from greenhouse_core.logic import IrrigationDecision, IrrigationLogic
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository


def decision_to_view(decision: IrrigationDecision) -> dict:
    """Render a decision for templates and JSON responses.

    Templates and the legacy JSON shape consume the decision via dict
    access (``decision["action"]``); model_dump preserves that contract
    while keeping the engine and persistence layers strictly typed.
    """
    payload = decision.model_dump(mode="json")
    payload["reason"] = decision.reason_text
    payload["primary_code"] = decision.primary_code.value if decision.primary_code else None
    return payload


class ClusterService:
    """Cluster status, history, and plant DB sync operations."""

    def __init__(self, repo: IrrigationRepository, plant_db: PlantDatabase):
        self._repo = repo
        self._plant_db = plant_db

    def get_cluster_status(self, cluster_id: int) -> dict | None:
        """Full cluster status: config, plants, sensors, irrigators, smart decision."""
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return None

        config = self._repo.get_irrigation_config(cluster_id)
        plants = self._repo.get_plants_in_cluster(cluster_id)
        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        irrigator = self._repo.get_irrigator_for_cluster(cluster_id)
        now = int(time.time())

        sensor_data = []
        for sensor in sensors:
            readings = self._repo.get_recent_readings(sensor.id, hours=24)
            last_reading = readings[0] if readings else None
            age = (now - last_reading.timestamp) if last_reading else None
            sensor_data.append(
                {
                    "id": sensor.id,
                    "name": sensor.name,
                    "type": sensor.type,
                    "plant_id": sensor.plant_id,
                    "last_reading": last_reading,
                    "reading_age_seconds": age,
                }
            )

        irrigator_data = None
        if irrigator is not None:
            events = self._repo.get_recent_events(irrigator.id, hours=48)
            irrigator_data = {
                "id": irrigator.id,
                "name": irrigator.name,
                "type": irrigator.type,
                "cluster_id": irrigator.cluster_id,
                "recent_event_count": len(events),
                "last_event": events[0] if events else None,
            }

        logic = IrrigationLogic(self._repo, self._plant_db)
        decision = logic.decide_for_cluster(cluster_id)  # no weather_client: status snapshot stays fast
        decision_dict = decision_to_view(decision) if decision else None

        return {
            "cluster": cluster,
            "config": config,
            "plants": plants,
            "sensors": sensor_data,
            "irrigator": irrigator_data,
            "decision": decision_dict,
        }

    def get_cluster_history(self, cluster_id: int, hours: int = 24, limit: int = 50) -> dict | None:
        """Get sensor readings + irrigation events for a cluster."""
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return None

        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        sensor_histories = []
        for sensor in sensors:
            readings = self._repo.get_recent_readings(sensor.id, hours=hours)
            sensor_histories.append(
                {
                    "sensor_id": sensor.id,
                    "sensor_name": sensor.name,
                    "readings": readings[:limit],
                }
            )

        irrigator = self._repo.get_irrigator_for_cluster(cluster_id)
        irrigator_histories = []
        if irrigator is not None:
            events = self._repo.get_recent_events(irrigator.id, hours=hours)
            irrigator_histories.append(
                {
                    "irrigator_id": irrigator.id,
                    "irrigator_name": irrigator.name,
                    "events": events[:limit],
                }
            )

        return {
            "cluster_name": cluster.name,
            "sensors": sensor_histories,
            "irrigators": irrigator_histories,
        }

    def sync_plant_with_db(self, plant) -> None:
        """Update a single plant with evidence-based care data."""
        care_data = self._plant_db.get_care_data(species=plant.species, category=plant.category)
        plant.water_needs = care_data.get("water_needs")
        plant.light_needs = care_data.get("light_needs")
        plant.ideal_temp_min = care_data.get("ideal_temp_min_c")
        plant.ideal_temp_max = care_data.get("ideal_temp_max_c")
        plant.ideal_humidity_min = care_data.get("ideal_humidity_min")
        plant.ideal_humidity_max = care_data.get("ideal_humidity_max")
        plant.notes = f"Sources: {', '.join(care_data.get('sources', [])[:2])}"
        self._repo.session.flush()
