"""Global search service — powers the Command-K palette."""

from sqlalchemy import func, or_, select

from greenhouse_core.models import Cluster, Irrigator, Plant, Sensor
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.schemas import SearchHit

# Maximum hits per entity type before the global cap is applied.
_PER_TYPE_LIMIT = 5


def search(repo: IrrigationRepository, q: str, limit: int = 20) -> list[SearchHit]:
    """Search clusters, plants, sensors, and irrigators by name/species/id prefix.

    Matches are case-insensitive SQL LIKE patterns. Results are capped at
    ``_PER_TYPE_LIMIT`` per entity type and then trimmed to ``limit`` total,
    distributing fairly across types.

    Args:
        repo: Active repository (wraps the current SQLAlchemy session).
        q: Search query string.
        limit: Maximum total hits returned (hard cap).

    Returns:
        List of SearchHit objects ordered clusters → plants → sensors → irrigators.
    """
    if not q or not q.strip():
        return []

    pattern = f"%{q}%"
    hits: list[SearchHit] = []
    hits.extend(_cluster_hits(repo, pattern))
    hits.extend(_plant_hits(repo, pattern))
    hits.extend(_sensor_hits(repo, q, pattern))
    hits.extend(_irrigator_hits(repo, q, pattern))
    return hits[:limit]


def _cluster_hits(repo: IrrigationRepository, pattern: str) -> list[SearchHit]:
    """Clusters whose name or location matches."""
    clusters = list(
        repo.session.scalars(
            select(Cluster)
            .where(
                or_(
                    func.lower(Cluster.name).like(func.lower(pattern)),
                    func.lower(Cluster.location).like(func.lower(pattern)),
                )
            )
            .limit(_PER_TYPE_LIMIT)
        )
    )
    return [
        SearchHit(
            entity_type="cluster",
            entity_id=c.id,
            label=c.name,
            sublabel=c.location,
            href=f"/clusters/{c.id}",
        )
        for c in clusters
    ]


def _plant_hits(repo: IrrigationRepository, pattern: str) -> list[SearchHit]:
    """Plants whose species or notes match."""
    plants = list(
        repo.session.scalars(
            select(Plant)
            .where(
                or_(
                    func.lower(Plant.species).like(func.lower(pattern)),
                    func.lower(Plant.notes).like(func.lower(pattern)),
                )
            )
            .limit(_PER_TYPE_LIMIT)
        )
    )
    return [
        SearchHit(
            entity_type="plant",
            entity_id=p.id,
            label=p.species,
            sublabel=_cluster_name(repo, p.cluster_id),
            href=f"/clusters/{p.cluster_id}/plants/{p.id}",
        )
        for p in plants
    ]


def _sensor_hits(repo: IrrigationRepository, q: str, pattern: str) -> list[SearchHit]:
    """Sensors whose name matches, or whose Tuya device id starts with the query."""
    sensors = list(
        repo.session.scalars(
            select(Sensor)
            .where(
                or_(
                    func.lower(Sensor.name).like(func.lower(pattern)),
                    Sensor.tuya_device_id.like(f"{q}%"),
                )
            )
            .limit(_PER_TYPE_LIMIT)
        )
    )
    return [
        SearchHit(
            entity_type="sensor",
            entity_id=s.id,
            label=s.name,
            sublabel=_cluster_name(repo, s.cluster_id),
            href=f"/clusters/{s.cluster_id}#sensor-{s.id}",
        )
        for s in sensors
    ]


def _irrigator_hits(repo: IrrigationRepository, q: str, pattern: str) -> list[SearchHit]:
    """Irrigators whose name matches, or whose Tuya device id starts with the query."""
    irrigators = list(
        repo.session.scalars(
            select(Irrigator)
            .where(
                or_(
                    func.lower(Irrigator.name).like(func.lower(pattern)),
                    Irrigator.tuya_device_id.like(f"{q}%"),
                )
            )
            .limit(_PER_TYPE_LIMIT)
        )
    )
    return [
        SearchHit(
            entity_type="irrigator",
            entity_id=i.id,
            label=i.name,
            sublabel=_cluster_name(repo, i.cluster_id),
            href=f"/clusters/{i.cluster_id}#irrigator-{i.id}",
        )
        for i in irrigators
    ]


def _cluster_name(repo: IrrigationRepository, cluster_id: int) -> str | None:
    """Return cluster name for a given id, or None if not found."""
    cluster = repo.get_cluster(cluster_id)
    return cluster.name if cluster else None
