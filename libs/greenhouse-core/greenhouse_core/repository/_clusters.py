"""Clusters and plants, with the plants' daily health scores."""

import time
from typing import Unpack

from sqlalchemy import and_, delete, func, or_, select, update

from greenhouse_core.constants import (
    SECONDS_PER_DAY,
)
from greenhouse_core.models import (
    ENTITY_PLANT,
    SOURCE_PLANT,
    Alert,
    Cluster,
    DecisionLog,
    EntityType,
    IrrigationWindow,
    Plant,
    PlantHealthDaily,
    Sensor,
    SensorAssignment,
    UserPreferences,
)
from greenhouse_core.repository._sensors import SensorMixin
from greenhouse_core.repository._types import (
    ClusterPatch,
    PlantCareFields,
    PlantHealthScores,
    PlantPatch,
    SameClusterMoveError,
)


class ClusterMixin(SensorMixin):
    """Clusters, plants and plant health."""

    # ── Clusters ──────────────────────────────────────────────────────────────

    def add_cluster(self, name: str, location: str | None = None, environment: str = "indoor") -> int:
        """Add a new cluster and return its ID."""
        cluster = Cluster(name=name, location=location, created_at=int(time.time()), environment=environment)
        self.session.add(cluster)
        self.session.flush()
        return cluster.id

    def get_cluster(self, cluster_id: int) -> Cluster | None:
        """Get a cluster by ID."""
        return self.session.get(Cluster, cluster_id)

    def list_clusters(self) -> list[Cluster]:
        """List all clusters ordered by name."""
        return list(self.session.scalars(select(Cluster).order_by(Cluster.name)))

    # ── Plants ────────────────────────────────────────────────────────────────

    def add_plant(
        self,
        cluster_id: int,
        species: str,
        **care: Unpack[PlantCareFields],
    ) -> int:
        """Add a plant to a cluster and return its ID.

        The id is allocated above every plant id the history still references:
        SQLite reuses the highest deleted rowid, and a recycled id would inherit
        a deleted plant's assignment windows (foreign keys are not enforced, so
        their ON DELETE CASCADE never runs).
        """
        plant = Plant(
            id=self._next_plant_id(),
            cluster_id=cluster_id,
            species=species,
            category=care.get("category"),
            water_needs=care.get("water_needs"),
            light_needs=care.get("light_needs"),
            ideal_temp_min=care.get("ideal_temp_min"),
            ideal_temp_max=care.get("ideal_temp_max"),
            ideal_humidity_min=care.get("ideal_humidity_min"),
            ideal_humidity_max=care.get("ideal_humidity_max"),
            notes=care.get("notes"),
        )
        self.session.add(plant)
        self.session.flush()
        return plant.id

    def _next_plant_id(self) -> int:
        """One above the highest plant id in ``plants`` or referenced by assignment history."""
        highest = max(
            self.session.scalar(select(func.max(Plant.id))) or 0,
            self.session.scalar(select(func.max(SensorAssignment.plant_id))) or 0,
        )
        return highest + 1

    def get_plants_in_cluster(self, cluster_id: int, *, include_archived: bool = False) -> list[Plant]:
        """Get the plants in a cluster ordered by species (archived ones only when asked)."""
        stmt = select(Plant).where(Plant.cluster_id == cluster_id).order_by(Plant.species)
        if not include_archived:
            stmt = stmt.where(Plant.archived_at.is_(None))
        return list(self.session.scalars(stmt))

    # ── Plant Health Daily ────────────────────────────────────────────────────

    def upsert_plant_health(
        self,
        plant_id: int,
        date_key: str,
        score: float,
        **scores: Unpack[PlantHealthScores],
    ) -> PlantHealthDaily:
        """Idempotent upsert for the per-plant daily health snapshot."""
        soil_in_band_pct, temp_in_band_pct = scores.get("soil_in_band_pct"), scores.get("temp_in_band_pct")
        humidity_in_band_pct, efficiency = scores.get("humidity_in_band_pct"), scores.get("efficiency")
        sample_count, timestamp = scores.get("sample_count", 0), scores.get("timestamp")
        sensor_ids = scores.get("sensor_ids")
        existing = self.session.scalar(
            select(PlantHealthDaily).where(PlantHealthDaily.plant_id == plant_id, PlantHealthDaily.date_key == date_key)
        )
        if existing:
            existing.score = score
            existing.soil_in_band_pct = soil_in_band_pct
            existing.temp_in_band_pct = temp_in_band_pct
            existing.humidity_in_band_pct = humidity_in_band_pct
            existing.efficiency = efficiency
            existing.sample_count = sample_count
            existing.sensor_ids = sensor_ids
            self.session.flush()
            return existing
        row = PlantHealthDaily(
            plant_id=plant_id,
            date_key=date_key,
            timestamp=timestamp or int(time.time()),
            score=score,
            soil_in_band_pct=soil_in_band_pct,
            temp_in_band_pct=temp_in_band_pct,
            humidity_in_band_pct=humidity_in_band_pct,
            efficiency=efficiency,
            sample_count=sample_count,
            sensor_ids=sensor_ids,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_plant_health_history(self, plant_id: int, days: int = 90) -> list[PlantHealthDaily]:
        """Last ``days`` of health snapshots oldest-first for charting."""
        cutoff = int(time.time()) - days * SECONDS_PER_DAY
        return list(
            self.session.scalars(
                select(PlantHealthDaily)
                .where(PlantHealthDaily.plant_id == plant_id, PlantHealthDaily.timestamp >= cutoff)
                .order_by(PlantHealthDaily.timestamp.asc())
            )
        )

    def list_all_plants(
        self,
        *,
        filter_cluster_id: int | None = None,
        filter_category: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
        include_archived: bool = False,
    ) -> list[Plant]:
        """All plants regardless of cluster, with optional filters and pagination.

        Args:
            filter_cluster_id: Restrict results to one cluster.
            filter_category: Restrict results to one plant category
                (e.g. ``"tropical"``).
            limit: Maximum rows to return; ``None`` means no cap.
            after_id: Id-based cursor — return only rows with ``id > after_id``.
            include_archived: Also return archived plants.

        Returns:
            Plants ordered by id ascending so cursor pagination is stable.
        """
        stmt = select(Plant).order_by(Plant.id)
        if not include_archived:
            stmt = stmt.where(Plant.archived_at.is_(None))
        if filter_cluster_id is not None:
            stmt = stmt.where(Plant.cluster_id == filter_cluster_id)
        if filter_category is not None:
            stmt = stmt.where(Plant.category == filter_category)
        return list(self.session.scalars(self._page(stmt, Plant.id, limit=limit, after_id=after_id)))

    def get_plant(self, plant_id: int) -> Plant | None:
        """Fetch a plant by id."""
        return self.session.get(Plant, plant_id)

    # ── Search (Command-K palette) ────────────────────────────────────────────
    # ``pattern`` is a SQL LIKE pattern matched case-insensitively; ``prefix`` is the raw
    # query, matched case-sensitively as a Tuya device-id prefix.

    def search_clusters(self, pattern: str, limit: int) -> list[Cluster]:
        """Clusters whose name or location matches ``pattern``."""
        return list(
            self.session.scalars(
                select(Cluster)
                .where(
                    or_(
                        func.lower(Cluster.name).like(func.lower(pattern)),
                        func.lower(Cluster.location).like(func.lower(pattern)),
                    )
                )
                .limit(limit)
            )
        )

    def search_plants(self, pattern: str, limit: int) -> list[Plant]:
        """Plants whose species or notes match ``pattern``."""
        return list(
            self.session.scalars(
                select(Plant)
                .where(
                    Plant.archived_at.is_(None),
                    or_(
                        func.lower(Plant.species).like(func.lower(pattern)),
                        func.lower(Plant.notes).like(func.lower(pattern)),
                    ),
                )
                .limit(limit)
            )
        )

    def update_cluster(self, cluster_id: int, **fields: Unpack[ClusterPatch]) -> Cluster | None:
        """Patch cluster fields; returns the updated row or None if missing."""
        cluster = self.session.get(Cluster, cluster_id)
        if not cluster:
            return None
        self._patch_fields(cluster, fields)
        self.session.flush()
        return cluster

    def delete_cluster(self, cluster_id: int) -> bool:
        """Delete a cluster and everything that points at it.

        The ORM cascades to plants, sensors (+ readings), the irrigator (+ events)
        and the config. The rows that reference the cluster by plain id or by an
        FK SQLite does not enforce — irrigation windows, decision logs, alerts,
        sensor assignments, its plants' daily health snapshots, a preferences
        ``default_cluster_id`` — are removed
        (or cleared) here, so API and web deletes leave no orphans. Activity
        events stay: they are the audit trail.
        """
        cluster = self.session.get(Cluster, cluster_id)
        if cluster is None:
            return False
        self._delete_cluster_references(cluster)
        self.session.delete(cluster)
        self.session.flush()
        return True

    def _delete_cluster_references(self, cluster: Cluster) -> None:
        """Remove the rows that point at ``cluster`` without an ORM cascade (see ``delete_cluster``)."""
        plant_ids = [p.id for p in cluster.plants]
        sensor_ids = [s.id for s in cluster.sensors]
        entity_ids = {
            EntityType.PLANT: plant_ids,
            EntityType.SENSOR: sensor_ids,
            EntityType.IRRIGATOR: [cluster.irrigator.id] if cluster.irrigator else [],
        }
        alert_scope = [Alert.cluster_id == cluster.id] + [
            and_(Alert.entity_type == kind, Alert.entity_id.in_(ids)) for kind, ids in entity_ids.items() if ids
        ]
        self.session.execute(delete(Alert).where(or_(*alert_scope)))
        self.session.execute(delete(IrrigationWindow).where(IrrigationWindow.cluster_id == cluster.id))
        self.session.execute(delete(DecisionLog).where(DecisionLog.cluster_id == cluster.id))
        self.session.execute(delete(PlantHealthDaily).where(PlantHealthDaily.plant_id.in_(plant_ids)))
        self.session.execute(
            delete(SensorAssignment).where(
                or_(SensorAssignment.sensor_id.in_(sensor_ids), SensorAssignment.plant_id.in_(plant_ids))
            )
        )
        prefs = self.session.scalar(select(UserPreferences).limit(1))
        if prefs is not None and prefs.default_cluster_id == cluster.id:
            prefs.default_cluster_id = None

    def update_plant(self, plant_id: int, **fields: Unpack[PlantPatch]) -> Plant | None:
        """Patch plant fields; returns the updated row or None."""
        plant = self.session.get(Plant, plant_id)
        if not plant:
            return None
        self._patch_fields(plant, fields)
        self.session.flush()
        return plant

    def delete_plant(self, plant_id: int) -> bool:
        """Delete a plant, closing its sensors' open assignments.

        Sensors retain their cluster; any open assignment to the plant is closed
        so historical readings stay attributed correctly. Its daily health
        snapshots go: SQLite reuses the id, and a new plant must not inherit them.
        """
        plant = self.session.get(Plant, plant_id)
        if not plant:
            return False
        when = int(time.time())
        for sensor in list(plant.sensors):
            # Close the open assignment before clearing the FK pointer.
            self._close_open_sensor_assignment(sensor.id, when=when)
            sensor.plant_id = None
        self.session.execute(delete(PlantHealthDaily).where(PlantHealthDaily.plant_id == plant_id))
        self.session.delete(plant)
        self.session.flush()
        return True

    def move_plant(self, plant_id: int, target_cluster_id: int) -> Plant | None:
        """Reassign a plant to a different cluster.

        Updates ``plants.cluster_id`` and reassigns every sensor linked to the
        plant (``sensors.plant_id == plant_id``) to the target cluster so the
        sensor readings — which key off ``sensor_id`` — surface under the new
        cluster's charts instead of the old one. Sensor probes are physically
        stuck in the plant's soil; in the real world they travel with the
        plant. The plant's own unresolved alerts (plant entity, keyed without
        the cluster) move with it, so the inbox lists them under the cluster
        the plant is in now. Decision logs, irrigation events, resolved alerts
        and sensor or cluster alerts are deliberately left attached to the
        original cluster so the audit trail reflects where the plant actually
        was at the time. Plant identity,
        plant_health_daily history, and learning profiles follow the plant
        because they key off ``plant_id``. Writes a ``plant_moved`` activity
        event with the before/after cluster ids and the list of sensor ids
        that travelled with the plant.

        Args:
            plant_id: Database id of the plant to move.
            target_cluster_id: Cluster the plant should belong to after the
                move.

        Returns:
            The updated ``Plant`` row, or ``None`` if either the plant or the
            target cluster does not exist.

        Raises:
            SameClusterMoveError: If the plant already belongs to
                ``target_cluster_id``.
        """
        plant = self.session.get(Plant, plant_id)
        if not plant:
            return None
        target = self.session.get(Cluster, target_cluster_id)
        if not target:
            return None
        if plant.cluster_id == target_cluster_id:
            msg = f"Plant {plant_id} already belongs to cluster {target_cluster_id}"
            raise SameClusterMoveError(msg)
        from_cluster_id = plant.cluster_id
        plant.cluster_id = target_cluster_id
        moved_sensors = list(self.session.scalars(select(Sensor).where(Sensor.plant_id == plant_id)))
        for sensor in moved_sensors:
            sensor.cluster_id = target_cluster_id
        self.session.execute(
            update(Alert)
            .where(Alert.entity_type == ENTITY_PLANT, Alert.entity_id == plant_id, Alert.status != "resolved")
            .values(cluster_id=target_cluster_id)
        )
        self.session.flush()
        self.add_activity_event(
            source=SOURCE_PLANT,
            entity_type=ENTITY_PLANT,
            entity_id=plant_id,
            code="plant_moved",
            message=f"moved plant {plant_id} from cluster {from_cluster_id} to cluster {target_cluster_id}",
            severity="info",
            payload={
                "from_cluster_id": from_cluster_id,
                "to_cluster_id": target_cluster_id,
                "sensor_ids": [s.id for s in moved_sensors],
            },
        )
        return plant
