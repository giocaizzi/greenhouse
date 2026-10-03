"""Cluster status and history services."""

import csv
import io
import time
from typing import TYPE_CHECKING, Any

from greenhouse_core.logic import IrrigationDecision, IrrigationLogic
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.utils import format_timestamp

if TYPE_CHECKING:
    from greenhouse_core.models import Irrigator, Plant, Sensor


class PlantNotFoundError(LookupError):
    """Raised by ``ClusterService.sync_plants`` when no cluster lists the requested plant id."""


class ClusterNotFoundError(LookupError):
    """Raised by ``ClusterService.sync_plants`` when the requested cluster id does not exist."""


def decision_to_view(decision: IrrigationDecision) -> dict[str, Any]:
    """Render a decision for templates and JSON responses.

    Templates and the legacy JSON shape consume the decision via dict
    access (``decision["action"]``); model_dump preserves that contract
    while keeping the engine and persistence layers strictly typed.
    """
    payload = decision.model_dump(mode="json")
    payload["reason"] = decision.reason_text
    payload["primary_code"] = decision.primary_code.value if decision.primary_code else None
    return payload


def cluster_events_csv(repo: IrrigationRepository, cluster_id: int, *, days: int) -> str:
    """Render a cluster's recent irrigation events as CSV text.

    The API and web CSV exports share it so both downloads keep one column layout.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "date", "time", "irrigator", "action", "duration_minutes", "triggered_by", "notes"])
    irrigator = repo.get_irrigator_for_cluster(cluster_id)
    if irrigator is not None:
        events = repo.get_recent_events(irrigator.id, hours=days * 24)
        for event in events:
            ts_str = format_timestamp(event.timestamp)
            date, _, time_part = ts_str.partition(" ")
            writer.writerow(
                [
                    event.timestamp,
                    date,
                    time_part,
                    irrigator.name,
                    event.action,
                    event.duration_minutes or "",
                    event.triggered_by,
                    event.notes or "",
                ]
            )

    output.seek(0)
    return output.getvalue()


class ClusterService:
    """Cluster status, history, and plant DB sync operations."""

    def __init__(self, repo: IrrigationRepository, plant_db: PlantDatabase):
        self._repo = repo
        self._plant_db = plant_db

    def get_cluster_status(self, cluster_id: int) -> dict[str, Any] | None:
        """Full cluster status: config, plants, sensors, irrigators, smart decision."""
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return None

        config = self._repo.get_irrigation_config(cluster_id)
        plants = self._repo.get_plants_in_cluster(cluster_id)
        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        irrigator = self._repo.get_irrigator_for_cluster(cluster_id)
        sensor_data = self._sensor_status_rows(sensors)
        irrigator_data = self._irrigator_status(irrigator)

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

    def _sensor_status_rows(self, sensors: "list[Sensor]") -> list[dict[str, Any]]:
        """One status row per sensor: its newest reading (24 h) and that reading's age."""
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
        return sensor_data

    def _irrigator_status(self, irrigator: "Irrigator | None") -> dict[str, Any] | None:
        """The irrigator's status dict (48 h event count, newest event, capacity), or ``None``."""
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
                # Hardware capacity drives vacation rationing; surface it so the
                # cluster detail irrigator row can show it without a refetch.
                "reservoir_l": irrigator.reservoir_l,
                "flow_rate_l_per_min": irrigator.flow_rate_l_per_min,
            }
        return irrigator_data

    def get_cluster_history(self, cluster_id: int, hours: int = 24, limit: int = 50) -> dict[str, Any] | None:
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

    def sync_plants(self, *, plant_id: int | None, cluster_id: int | None) -> tuple[int, list[str]]:
        """Refresh care data for one plant, one cluster or every cluster; return ``(synced, errors)``.

        Shared by the API and web plant-DB sync. A truthy ``plant_id`` wins and its failure
        propagates; otherwise per-plant failures are collected and the run continues. The
        caller commits.

        Raises:
            PlantNotFoundError: ``plant_id`` is set and no cluster lists that plant.
            ClusterNotFoundError: ``plant_id`` is not set, ``cluster_id`` is, and no such cluster exists.
        """
        if plant_id:
            plant = self._find_plant_in_clusters(plant_id)
            if not plant:
                raise PlantNotFoundError(plant_id)
            self.sync_plant_with_db(plant)
            return 1, []
        return self._sync_cluster_plants(cluster_id)

    def _find_plant_in_clusters(self, plant_id: int) -> "Plant | None":
        """Scan every cluster's plants (not ``get_plant``: an orphan plant must stay "not found")."""
        clusters = self._repo.list_clusters()
        plant = None
        for cluster in clusters:
            for p in self._repo.get_plants_in_cluster(cluster.id):
                if p.id == plant_id:
                    plant = p
                    break
            if plant:
                break
        return plant

    def _sync_cluster_plants(self, cluster_id: int | None) -> tuple[int, list[str]]:
        """Sync every plant of one cluster (or of all clusters), collecting per-plant errors."""
        errors: list[str] = []
        synced = 0
        if cluster_id:
            cluster = self._repo.get_cluster(cluster_id)
            if cluster is None:
                raise ClusterNotFoundError(cluster_id)
            clusters = [cluster]
        else:
            clusters = self._repo.list_clusters()
        for cluster in clusters:
            for plant in self._repo.get_plants_in_cluster(cluster.id):
                try:
                    self.sync_plant_with_db(plant)
                    synced += 1
                except Exception as e:
                    errors.append(f"{plant.species}: {e}")
        return synced, errors

    def sync_plant_with_db(self, plant: "Plant") -> None:
        """Update a single plant with evidence-based care data."""
        care_data = self._plant_db.get_care_data(species=plant.species, category=plant.category)
        plant.water_needs = care_data.get("water_needs")
        plant.light_needs = care_data.get("light_needs")
        plant.ideal_temp_min = care_data.get("ideal_temp_min_c")
        plant.ideal_temp_max = care_data.get("ideal_temp_max_c")
        plant.ideal_humidity_min = care_data.get("ideal_humidity_min")
        plant.ideal_humidity_max = care_data.get("ideal_humidity_max")
        plant.notes = f"Sources: {', '.join(care_data.get('sources', [])[:2])}"
        self._repo.flush()
