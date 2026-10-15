"""Soft delete for sensors and plants: archive, restore, and purge only after archive (issue #149).

An archived row keeps its id, its readings and its assignment history, and drops out of
every default listing — so sync, the health monitor, snapshots and decisions skip it.
Each step writes an activity event naming the row (for a sensor: its ``tuya_device_id``,
never its ``local_key``), so the trail survives a purge.
"""

import time

from sqlalchemy import delete, func, select

from greenhouse_core.models import (
    ENTITY_PLANT,
    ENTITY_SENSOR,
    SOURCE_PLANT,
    SOURCE_SENSOR,
    Plant,
    Sensor,
    SensorAssignment,
    SensorReading,
)
from greenhouse_core.repository._clusters import ClusterMixin
from greenhouse_core.repository._types import NotArchivedError


class ArchiveMixin(ClusterMixin):
    """Archive / restore / purge of sensors and plants, each with its audit event."""

    # ── Sensors ───────────────────────────────────────────────────────────────

    def archive_sensor(self, sensor_id: int) -> Sensor | None:
        """Archive a sensor (idempotent): close its open assignment, keep its readings and plant pointer.

        The pointer stays so a restore can re-link the sensor to the same plant.
        """
        sensor = self.session.get(Sensor, sensor_id)
        if sensor is None or sensor.archived_at is not None:
            return sensor
        now = int(time.time())
        self._close_open_sensor_assignment(sensor_id, when=now)
        sensor.archived_at = now
        self.session.flush()
        self._audit_sensor(sensor, "sensor_archived", "archived", now)
        return sensor

    def restore_sensor(self, sensor_id: int) -> Sensor | None:
        """Bring an archived sensor back (idempotent), re-linked to its plant when that plant is still active."""
        sensor = self.session.get(Sensor, sensor_id)
        if sensor is None or sensor.archived_at is None:
            return sensor
        now = int(time.time())
        sensor.archived_at = None
        plant = self.session.get(Plant, sensor.plant_id) if sensor.plant_id is not None else None
        if plant is not None and plant.archived_at is None:
            self._open_sensor_assignment(sensor_id, plant.id, when=now)
        else:
            sensor.plant_id = None
        self.session.flush()
        self._audit_sensor(sensor, "sensor_restored", "restored", now)
        return sensor

    def purge_sensor(self, sensor_id: int) -> bool:
        """Hard-delete an archived sensor with its readings and assignments, freeing its tuya_device_id.

        Raises:
            NotArchivedError: the sensor is not archived (archive it first).
        """
        sensor = self.session.get(Sensor, sensor_id)
        if sensor is None:
            return False
        if sensor.archived_at is None:
            msg = f"sensor {sensor_id} is not archived: archive it first"
            raise NotArchivedError(msg)
        self._audit_sensor(sensor, "sensor_purged", "purged", int(time.time()))
        self.session.execute(delete(SensorAssignment).where(SensorAssignment.sensor_id == sensor_id))
        self.session.delete(sensor)
        self.session.flush()
        return True

    def _audit_sensor(self, sensor: Sensor, code: str, verb: str, ts: int) -> None:
        readings = self.session.scalar(select(func.count()).where(SensorReading.sensor_id == sensor.id)) or 0
        self.add_activity_event(
            source=SOURCE_SENSOR,
            entity_type=ENTITY_SENSOR,
            entity_id=sensor.id,
            code=code,
            message=f"{verb} sensor {sensor.name} ({sensor.tuya_device_id}), {readings} readings",
            severity="info",
            payload={
                "name": sensor.name,
                "tuya_device_id": sensor.tuya_device_id,
                "cluster_id": sensor.cluster_id,
                "plant_id": sensor.plant_id,
                "reading_count": readings,
            },
            timestamp=ts,
        )

    # ── Plants ────────────────────────────────────────────────────────────────

    def archive_plant(self, plant_id: int) -> Plant | None:
        """Archive a plant (idempotent): unlink its sensors (they stay in the cluster), keep its history."""
        plant = self.session.get(Plant, plant_id)
        if plant is None or plant.archived_at is not None:
            return plant
        now = int(time.time())
        sensor_ids = []
        for sensor in list(plant.sensors):
            self._close_open_sensor_assignment(sensor.id, when=now)
            sensor.plant_id = None
            sensor_ids.append(sensor.id)
        plant.archived_at = now
        self.session.flush()
        self._audit_plant(plant, "plant_archived", "archived", now, sensor_ids)
        return plant

    def restore_plant(self, plant_id: int) -> Plant | None:
        """Bring an archived plant back (idempotent); its former sensors are not re-linked."""
        plant = self.session.get(Plant, plant_id)
        if plant is None or plant.archived_at is None:
            return plant
        plant.archived_at = None
        self.session.flush()
        self._audit_plant(plant, "plant_restored", "restored", int(time.time()), [])
        return plant

    def purge_plant(self, plant_id: int) -> bool:
        """Hard-delete an archived plant (and its health snapshots).

        Raises:
            NotArchivedError: the plant is not archived (archive it first).
        """
        plant = self.session.get(Plant, plant_id)
        if plant is None:
            return False
        if plant.archived_at is None:
            msg = f"plant {plant_id} is not archived: archive it first"
            raise NotArchivedError(msg)
        self._audit_plant(plant, "plant_purged", "purged", int(time.time()), [])
        return self.delete_plant(plant_id)

    def _audit_plant(self, plant: Plant, code: str, verb: str, ts: int, sensor_ids: list[int]) -> None:
        readings = len(self.readings_for_plant(plant.id, since_ts=0))
        self.add_activity_event(
            source=SOURCE_PLANT,
            entity_type=ENTITY_PLANT,
            entity_id=plant.id,
            code=code,
            message=f"{verb} plant {plant.species}, {readings} attributed readings",
            severity="info",
            payload={
                "species": plant.species,
                "cluster_id": plant.cluster_id,
                "unlinked_sensor_ids": sensor_ids,
                "reading_count": readings,
            },
            timestamp=ts,
        )
