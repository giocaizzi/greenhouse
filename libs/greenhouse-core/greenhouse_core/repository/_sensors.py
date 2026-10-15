"""Sensors, their plant-assignment history and their readings."""

import json
import time
from typing import TYPE_CHECKING, Any, Unpack, cast

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from greenhouse_core.constants import (
    RESPONSE_POST_WINDOW_SECONDS,
    RESPONSE_PRE_WINDOW_SECONDS,
    SECONDS_PER_HOUR,
)
from greenhouse_core.models import (
    Sensor,
    SensorReading,
)
from greenhouse_core.repository._assignments import AssignmentMixin
from greenhouse_core.repository._types import (
    ReadingValues,
    SensorPatch,
    SensorPlantLink,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.engine import CursorResult


class SensorMixin(AssignmentMixin):
    """Sensors, sensor assignments and sensor readings."""

    # ── Sensors ───────────────────────────────────────────────────────────────

    def add_sensor(
        self,
        cluster_id: int,
        tuya_device_id: str,
        name: str,
        sensor_type: str,
        config: dict[str, Any],
        **link: Unpack[SensorPlantLink],
    ) -> int:
        """Add a sensor device and return its ID.

        When ``plant_id`` is set, opens a sensor_assignment so historical
        attribution starts at ``assignment_started_at`` (defaults to "now").
        Pass ``assignment_started_at=0`` (or any earlier timestamp) when
        importing a sensor whose existing readings predate this row — the
        migration backfill uses the same convention.
        """
        plant_id = link.get("plant_id")
        assignment_started_at = link.get("assignment_started_at")
        sensor = Sensor(
            cluster_id=cluster_id,
            tuya_device_id=tuya_device_id,
            name=name,
            type=sensor_type,
            config=json.dumps(config),
            plant_id=plant_id,
        )
        self.session.add(sensor)
        self.session.flush()
        if plant_id is not None:
            ts = assignment_started_at if assignment_started_at is not None else int(time.time())
            self._open_sensor_assignment(sensor.id, plant_id, when=ts)
        return sensor.id

    def get_sensors_in_cluster(self, cluster_id: int) -> list[Sensor]:
        """Get all sensors in a cluster ordered by name."""
        return list(self.session.scalars(select(Sensor).where(Sensor.cluster_id == cluster_id).order_by(Sensor.name)))

    # ── Sensor Readings ───────────────────────────────────────────────────────

    def add_sensor_reading(
        self,
        sensor_id: int,
        **values: Unpack[ReadingValues],
    ) -> int | None:
        """Add a sensor reading. Deduplicates by (sensor_id, timestamp).

        Returns row ID if inserted, None if duplicate skipped.
        """
        ts = values.get("timestamp") or int(time.time())
        stmt = (
            sqlite_insert(SensorReading)
            .values(
                sensor_id=sensor_id,
                timestamp=ts,
                temperature=values.get("temperature"),
                soil_moisture=values.get("soil_moisture"),
                light=values.get("light"),
                env_humidity=values.get("env_humidity"),
                battery_state=values.get("battery_state"),
                water_warning=values.get("water_warning"),
            )
            .on_conflict_do_nothing(index_elements=["sensor_id", "timestamp"])
        )
        result = cast("CursorResult[Any]", self.session.execute(stmt))
        self.session.flush()
        if result.rowcount > 0:
            # Fetch the inserted row's ID
            return self.session.execute(
                select(SensorReading.id).where(SensorReading.sensor_id == sensor_id, SensorReading.timestamp == ts)
            ).scalar_one()
        return None

    def get_last_reading_timestamp(self, sensor_id: int) -> int | None:
        """Get timestamp of the most recent reading for a sensor."""
        return self.session.scalar(
            select(func.max(SensorReading.timestamp)).where(SensorReading.sensor_id == sensor_id)
        )

    def get_latest_reading(self, sensor_id: int) -> SensorReading | None:
        """Return the sensor's most recent persisted reading, or ``None``.

        The single read-model entry point for consumers that need a sensor's
        current state (health derivation, pipeline freshness) without touching
        the Cloud — the sync job is the sole writer of these rows.
        """
        return self.session.scalar(
            select(SensorReading)
            .where(SensorReading.sensor_id == sensor_id)
            .order_by(SensorReading.timestamp.desc())
            .limit(1)
        )

    def bulk_add_sensor_readings(
        self,
        readings: list[
            tuple[
                int,
                int,
                float | None,
                float | None,
                int | None,
                float | None,
                str | None,
                int | None,
            ]
        ],
    ) -> int:
        """Bulk insert sensor readings with dedup. Returns count of new rows inserted.

        Each tuple: (sensor_id, timestamp, temperature, soil_moisture, light,
                     env_humidity, battery_state, water_warning)
        """
        if not readings:
            return 0
        inserted = 0
        for r in readings:
            stmt = (
                sqlite_insert(SensorReading)
                .values(
                    sensor_id=r[0],
                    timestamp=r[1],
                    temperature=r[2],
                    soil_moisture=r[3],
                    light=r[4],
                    env_humidity=r[5],
                    battery_state=r[6],
                    water_warning=r[7],
                )
                .on_conflict_do_nothing(index_elements=["sensor_id", "timestamp"])
            )
            result = cast("CursorResult[Any]", self.session.execute(stmt))
            inserted += result.rowcount
        self.session.flush()
        return inserted

    def get_recent_readings(self, sensor_id: int, hours: int = 24) -> list[SensorReading]:
        """Get recent readings for a sensor, ordered by timestamp DESC."""
        cutoff = int(time.time()) - (hours * SECONDS_PER_HOUR)
        return list(
            self.session.scalars(
                select(SensorReading)
                .where(SensorReading.sensor_id == sensor_id, SensorReading.timestamp >= cutoff)
                .order_by(SensorReading.timestamp.desc())
            )
        )

    def get_judged_readings(self, sensor_id: int, hours: int = 24) -> list[SensorReading]:
        """``get_recent_readings`` (DESC) limited to the sensor's current link, settle window excluded.

        The read every judgement about a sensor's current plant goes through, so
        readings measured in another pot before a move never count (issue #145).
        """
        since = self.judged_since(sensor_id)
        return [r for r in self.get_recent_readings(sensor_id, hours=hours) if r.timestamp >= since]

    def get_readings_around(
        self,
        sensor_id: int,
        timestamp: int,
        before_seconds: int = RESPONSE_PRE_WINDOW_SECONDS,
        after_seconds: int = RESPONSE_POST_WINDOW_SECONDS,
    ) -> tuple[list[SensorReading], list[SensorReading]]:
        """Get readings before and after a timestamp.

        Returns (before_readings, after_readings), each ordered by timestamp ASC.
        """
        before = list(
            self.session.scalars(
                select(SensorReading)
                .where(
                    SensorReading.sensor_id == sensor_id,
                    SensorReading.timestamp >= timestamp - before_seconds,
                    SensorReading.timestamp <= timestamp,
                )
                .order_by(SensorReading.timestamp.asc())
            )
        )
        after = list(
            self.session.scalars(
                select(SensorReading)
                .where(
                    SensorReading.sensor_id == sensor_id,
                    SensorReading.timestamp >= timestamp,
                    SensorReading.timestamp <= timestamp + after_seconds,
                )
                .order_by(SensorReading.timestamp.asc())
            )
        )
        return before, after

    def list_all_sensors(
        self,
        *,
        filter_cluster_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> list[Sensor]:
        """All sensors regardless of cluster (used by health pulse, anomaly).

        Args:
            filter_cluster_id: Restrict results to one cluster.
            limit: Maximum rows to return; ``None`` means no cap.
            after_id: Id-based cursor — return only rows with ``id > after_id``.

        Returns:
            Sensors ordered by id ascending so cursor pagination is stable.
        """
        stmt = select(Sensor).order_by(Sensor.id)
        if filter_cluster_id is not None:
            stmt = stmt.where(Sensor.cluster_id == filter_cluster_id)
        return list(self.session.scalars(self._page(stmt, Sensor.id, limit=limit, after_id=after_id)))

    def get_sensor(self, sensor_id: int) -> Sensor | None:
        """Fetch a sensor by id."""
        return self.session.get(Sensor, sensor_id)

    def list_sensors_by_ids(self, sensor_ids: "Iterable[int]") -> list[Sensor]:
        """The sensors whose id is in ``sensor_ids``, in database order; unknown ids are skipped."""
        return list(self.session.scalars(select(Sensor).where(Sensor.id.in_(sensor_ids))))

    # ── Search (Command-K palette) ────────────────────────────────────────────
    # ``pattern`` is a SQL LIKE pattern matched case-insensitively; ``prefix`` is the raw
    # query, matched case-sensitively as a Tuya device-id prefix.

    def search_sensors(self, pattern: str, prefix: str, limit: int) -> list[Sensor]:
        """Sensors whose name matches ``pattern`` or whose Tuya device id starts with ``prefix``."""
        return list(
            self.session.scalars(
                select(Sensor)
                .where(
                    or_(
                        func.lower(Sensor.name).like(func.lower(pattern)),
                        Sensor.tuya_device_id.like(f"{prefix}%"),
                    )
                )
                .limit(limit)
            )
        )

    def update_sensor(self, sensor_id: int, **fields: Unpack[SensorPatch]) -> Sensor | None:
        """Patch sensor fields, keeping the assignment history in sync.

        ``plant_id`` changes are routed through ``reassign_sensor_to_plant``;
        ``config`` is JSON-serialised if a dict.
        """
        sensor = self.session.get(Sensor, sensor_id)
        if not sensor:
            return None
        # Pull plant_id out and apply it via the assignment-aware path. Plant
        # reassignment must NOT be possible via a raw setattr — that would
        # silently lose history.
        plant_id_in_fields = "plant_id" in fields
        new_plant_id = fields.pop("plant_id", None) if plant_id_in_fields else None
        self._patch_fields(sensor, fields, json_fields=frozenset({"config"}))
        if plant_id_in_fields:
            self.reassign_sensor_to_plant(sensor_id, new_plant_id)
        self.session.flush()
        return sensor

    def delete_sensor(self, sensor_id: int) -> bool:
        """Delete a sensor (cascades to its readings)."""
        return self._delete_by_id(Sensor, sensor_id)
