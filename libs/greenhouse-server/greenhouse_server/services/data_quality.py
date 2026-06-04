"""Data-quality audit: detect configuration gaps, stale sensors, duplicate IDs."""

import time
from collections import Counter

from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.schemas import DataQualityIssue, DataQualityReport

_STALE_THRESHOLD = 24 * 3600


def build_report(repo: IrrigationRepository, plant_db: PlantDatabase) -> DataQualityReport:
    """Scan the database and return a full data-quality report.

    Args:
        repo: Active repository session.
        plant_db: Plant species database for unknown-species checks.

    Returns:
        DataQualityReport with all detected issues and per-code counts.
    """
    now = int(time.time())
    issues: list[DataQualityIssue] = []

    sensors = repo.list_all_sensors()
    irrigators = repo.list_all_irrigators()
    plants = repo.list_all_plants()
    clusters = repo.list_clusters()

    plant_ids_with_sensor: set[int] = set()

    for sensor in sensors:
        if sensor.plant_id is None:
            issues.append(
                DataQualityIssue(
                    code="sensor_without_plant",
                    severity="warning",
                    entity_type="sensor",
                    entity_id=sensor.id,
                    label=sensor.name,
                    message=f"Sensor '{sensor.name}' is not assigned to any plant.",
                )
            )
        else:
            plant_ids_with_sensor.add(sensor.plant_id)

        last_ts = repo.get_last_reading_timestamp(sensor.id)
        age = (now - last_ts) if last_ts else None
        if age is None or age > _STALE_THRESHOLD:
            issues.append(
                DataQualityIssue(
                    code="stale_sensor",
                    severity="warning",
                    entity_type="sensor",
                    entity_id=sensor.id,
                    label=sensor.name,
                    message=f"Sensor '{sensor.name}' has no reading in the last 24 h.",
                )
            )

    for plant in plants:
        if plant.id not in plant_ids_with_sensor:
            issues.append(
                DataQualityIssue(
                    code="plant_without_sensor",
                    severity="warning",
                    entity_type="plant",
                    entity_id=plant.id,
                    label=plant.species,
                    message=f"Plant '{plant.species}' has no sensor assigned.",
                )
            )
        known = plant_db.lookup_species(plant.species) is not None
        if not known:
            issues.append(
                DataQualityIssue(
                    code="unknown_species",
                    severity="warning",
                    entity_type="plant",
                    entity_id=plant.id,
                    label=plant.species,
                    message=f"Species '{plant.species}' is not in the plant database.",
                )
            )

    for cluster in clusters:
        cluster_plants = repo.get_plants_in_cluster(cluster.id)
        cluster_irrigator = repo.get_irrigator_for_cluster(cluster.id)
        if cluster_irrigator and not cluster_plants:
            issues.append(
                DataQualityIssue(
                    code="irrigator_in_empty_cluster",
                    severity="warning",
                    entity_type="cluster",
                    entity_id=cluster.id,
                    label=cluster.name,
                    message=f"Cluster '{cluster.name}' has irrigators but no plants.",
                )
            )
        if repo.get_irrigation_config(cluster.id) is None:
            issues.append(
                DataQualityIssue(
                    code="cluster_without_config",
                    severity="warning",
                    entity_type="cluster",
                    entity_id=cluster.id,
                    label=cluster.name,
                    message=f"Cluster '{cluster.name}' has no irrigation config.",
                )
            )

    all_devices = [(s.tuya_device_id, "sensor", s.id, s.name) for s in sensors] + [
        (i.tuya_device_id, "irrigator", i.id, i.name) for i in irrigators
    ]
    tuya_id_counts: Counter[str] = Counter(dev[0] for dev in all_devices)
    seen_dupes: set[str] = set()
    for tid, entity_type, entity_id, label in all_devices:
        if tuya_id_counts[tid] > 1 and tid not in seen_dupes:
            seen_dupes.add(tid)
            issues.append(
                DataQualityIssue(
                    code="duplicate_tuya_id",
                    severity="critical",
                    entity_type=entity_type,
                    entity_id=entity_id,
                    label=label,
                    message=f"Tuya device ID '{tid}' is shared by {tuya_id_counts[tid]} devices.",
                )
            )

    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.code] = counts.get(issue.code, 0) + 1

    return DataQualityReport(issues=issues, counts=counts)
