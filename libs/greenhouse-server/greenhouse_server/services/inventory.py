"""Device registration rules shared by the JSON API and the web UI.

Both surfaces register irrigators (and sensors) through these functions so the
validation, the duplicate-device handling and the capacity follow-up are one
implementation. Each caller keeps its own I/O mapping (status code, detail or
template) and commits on success; on a refusal the session is already rolled back.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError

from greenhouse_core.repository import IrrigationRepository, IrrigatorExistsError

__all__ = [
    "DeviceIdExistsError",
    "IrrigatorExistsError",
    "PlantNotInClusterError",
    "create_irrigator",
    "create_sensor",
    "ensure_plant_in_cluster",
]


class DeviceIdExistsError(LookupError):
    """The Tuya device id is already registered (the database's unique constraint refused it)."""


class PlantNotInClusterError(LookupError):
    """A sensor was linked to a plant that does not belong to the sensor's cluster."""

    def __init__(self, plant_id: int) -> None:
        super().__init__(plant_id)
        self.plant_id = plant_id


def create_irrigator(
    repo: IrrigationRepository,
    cluster_id: int,
    *,
    tuya_device_id: str,
    name: str,
    irrigator_type: str,
    config: dict[str, Any],
    reservoir_l: float | None = None,
    flow_rate_l_per_min: float | None = None,
) -> int:
    """Add the cluster's irrigator, then persist any capacity values; return its id.

    ``add_irrigator`` does not take the capacity columns, so they are written in a
    follow-up update when either is supplied. The caller commits.

    Raises:
        IrrigatorExistsError: the cluster already has an irrigator (session rolled back).
        DeviceIdExistsError: the Tuya device id is already registered (session rolled back).
    """
    try:
        irrigator_id = repo.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=tuya_device_id,
            name=name,
            irrigator_type=irrigator_type,
            config=config,
        )
    except IrrigatorExistsError:
        repo.session.rollback()
        raise
    except IntegrityError:
        repo.session.rollback()
        raise DeviceIdExistsError(tuya_device_id) from None
    if reservoir_l is not None or flow_rate_l_per_min is not None:
        repo.update_irrigator(irrigator_id, reservoir_l=reservoir_l, flow_rate_l_per_min=flow_rate_l_per_min)
    return irrigator_id


def ensure_plant_in_cluster(repo: IrrigationRepository, cluster_id: int, plant_id: int | None) -> None:
    """Refuse a sensor's plant link unless the plant is one of the cluster's plants.

    A falsy ``plant_id`` (``None`` / ``0``) means "no plant" and always passes.

    Raises:
        PlantNotInClusterError: the plant is unknown or lives in another cluster.
    """
    if plant_id and not any(p.id == plant_id for p in repo.get_plants_in_cluster(cluster_id)):
        raise PlantNotInClusterError(plant_id)


def create_sensor(
    repo: IrrigationRepository,
    cluster_id: int,
    *,
    tuya_device_id: str,
    name: str,
    sensor_type: str,
    config: dict[str, Any],
    plant_id: int | None = None,
) -> int:
    """Register a sensor under a cluster, optionally linked to one of its plants; return its id.

    The caller commits.

    Raises:
        PlantNotInClusterError: ``plant_id`` is not one of the cluster's plants (nothing written).
        DeviceIdExistsError: the Tuya device id is already registered (session rolled back).
    """
    ensure_plant_in_cluster(repo, cluster_id, plant_id)
    try:
        return repo.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=tuya_device_id,
            name=name,
            sensor_type=sensor_type,
            config=config,
            plant_id=plant_id,
        )
    except IntegrityError:
        repo.session.rollback()
        raise DeviceIdExistsError(tuya_device_id) from None
