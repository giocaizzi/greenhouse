"""Sensor → plant assignment history: which plant a sensor measured, and when.

Attribution rule (issue #145): a reading belongs to the plant whose assignment
window holds its timestamp; ``Sensor.plant_id`` is only the "who is it now"
pointer. A sensor's *first* assignment also covers the readings stored before
it (the Tuya log backfills history from before the sensor was registered in its
pot), so only a move — a later assignment, or an unlink — opens a boundary.
Judgements additionally skip the ``SENSOR_MOVE_SETTLE_SECONDS`` after a boundary,
while the moved probe settles in its new soil; charts show those rows raw.
"""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from greenhouse_core.constants import SENSOR_MOVE_SETTLE_SECONDS
from greenhouse_core.models import (
    ENTITY_SENSOR,
    SOURCE_SENSOR,
    Sensor,
    SensorAssignment,
    SensorReading,
)
from greenhouse_core.repository._audit import AuditMixin
from greenhouse_core.repository._types import InvalidMoveTimeError

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class _Window:
    """One assignment row as an attribution window: ``[starts, ends)``, ``ends=None`` = still open."""

    sensor_id: int
    plant_id: int
    starts: int
    ends: int | None

    def holds(self, ts: int) -> bool:
        """True when a reading at ``ts`` falls inside the window."""
        return self.starts <= ts and (self.ends is None or ts < self.ends)


def _windows(rows: "Sequence[SensorAssignment]", *, settle: int) -> list[_Window]:
    """One sensor's rows (oldest first) as windows: the first reaches back to 0, later ones start after ``settle``."""
    return [
        _Window(row.sensor_id, row.plant_id, 0 if i == 0 else row.started_at + settle, row.ended_at)
        for i, row in enumerate(rows)
    ]


def _link_start(rows: "Sequence[SensorAssignment]") -> int | None:
    """When the sensor's current state began: the open row's start, the last unlink, or None if never linked."""
    if not rows:
        return None
    last = rows[-1]
    return last.started_at if last.ended_at is None else last.ended_at


def _check_redate_floor(prev: SensorAssignment | None, *, touching: bool, when: int) -> None:
    """Refuse a re-dated boundary that would empty or overlap the previous row."""
    if prev is None:
        return
    if touching and when <= prev.started_at:
        msg = f"must be after the previous assignment's start ({prev.started_at})"
        raise InvalidMoveTimeError(msg)
    if not touching and prev.ended_at is not None and when < prev.ended_at:
        msg = f"must be at or after the previous assignment's end ({prev.ended_at})"
        raise InvalidMoveTimeError(msg)


class AssignmentMixin(AuditMixin):
    """Sensor assignment rows and the readings they attribute to a plant."""

    # Sensor.plant_id is the current pointer; SensorAssignment is the history.
    # The two are kept in lock-step by reassign_sensor_to_plant() / move_sensor() —
    # never mutate Sensor.plant_id directly outside the repository.

    def _open_sensor_assignment(self, sensor_id: int, plant_id: int, *, when: int) -> SensorAssignment:
        row = SensorAssignment(sensor_id=sensor_id, plant_id=plant_id, started_at=when, ended_at=None)
        self.session.add(row)
        self.session.flush()
        return row

    def _close_open_sensor_assignment(self, sensor_id: int, *, when: int) -> None:
        open_row = self.session.scalars(
            select(SensorAssignment).where(
                SensorAssignment.sensor_id == sensor_id,
                SensorAssignment.ended_at.is_(None),
            )
        ).first()
        if open_row is not None and open_row.ended_at is None:
            open_row.ended_at = when
            self.session.flush()

    def reassign_sensor_to_plant(self, sensor_id: int, new_plant_id: int | None, *, when: int | None = None) -> None:
        """Atomically move a sensor to ``new_plant_id`` (or detach with None).

        Closes any open assignment row for the sensor and, when ``new_plant_id``
        is not None, opens a fresh one. The sensor's ``plant_id`` pointer is
        updated to match. No-ops when the requested plant is already the
        current assignment, so callers can use this as an idempotent setter.
        Emits a ``sensor_reassigned`` activity event with the before/after plant
        ids so the timeline can explain why a plant's chart suddenly changes
        shape after a sensor move.
        """
        sensor = self.session.get(Sensor, sensor_id)
        if sensor is None:
            return
        if sensor.plant_id == new_plant_id:
            return
        ts = when if when is not None else int(time.time())
        from_plant_id = sensor.plant_id
        self._close_open_sensor_assignment(sensor_id, when=ts)
        sensor.plant_id = new_plant_id
        if new_plant_id is not None:
            self._open_sensor_assignment(sensor_id, new_plant_id, when=ts)
        self.session.flush()
        self.add_activity_event(
            source=SOURCE_SENSOR,
            entity_type=ENTITY_SENSOR,
            entity_id=sensor_id,
            code="sensor_reassigned",
            message=f"reassigned sensor {sensor_id} from plant {from_plant_id} to plant {new_plant_id}",
            severity="info",
            payload={"from_plant_id": from_plant_id, "to_plant_id": new_plant_id},
            timestamp=ts,
        )

    def move_sensor(self, sensor_id: int, plant_id: int | None, *, when: int) -> None:
        """Move a sensor to ``plant_id`` (None = detach) effective ``when``, or re-date its last move.

        A different plant closes the current link at ``when`` and opens the new one
        (``when`` may not precede the current link's start). The same plant re-dates
        the boundary that opened the current link — the open row's start and the
        previous row's end together — so a move entered late attributes the
        readings in between to the right plant.

        Raises:
            InvalidMoveTimeError: ``when`` would precede the current link or empty
                or overlap the previous assignment.
        """
        sensor = self.session.get(Sensor, sensor_id)
        if sensor is None:
            return
        rows = self.list_sensor_assignments(sensor_id)
        if sensor.plant_id == plant_id:
            self._redate_link(sensor, rows, when)
            return
        if len(rows) == 1 and rows[0].ended_at is None and when < rows[0].started_at:
            rows[0].started_at = when  # the first link reaches back anyway: its start is notional
        floor = _link_start(rows)
        if floor is not None and when < floor:
            msg = f"must be at or after the current assignment's start ({floor})"
            raise InvalidMoveTimeError(msg)
        self.reassign_sensor_to_plant(sensor_id, plant_id, when=when)

    def _redate_link(self, sensor: Sensor, rows: "Sequence[SensorAssignment]", when: int) -> None:
        """Shift the boundary that opened the sensor's current link to ``when``."""
        if not rows:
            return  # never linked: there is no move to re-date
        last = rows[-1]
        if last.ended_at is None:
            prev = rows[-2] if len(rows) > 1 else None
            touching = prev is not None and prev.ended_at == last.started_at
            if prev is rows[0] and touching and when <= prev.started_at:
                prev.started_at = when  # the first link reaches back anyway: its start is notional
            else:
                _check_redate_floor(prev, touching=touching, when=when)
            if prev is not None and touching:
                prev.ended_at = when
            redated_from = last.started_at
            last.started_at = when
        else:  # detached: the boundary is the unlink itself
            _check_redate_floor(last, touching=True, when=when)
            redated_from = last.ended_at
            last.ended_at = when
        self.session.flush()
        self.add_activity_event(
            source=SOURCE_SENSOR,
            entity_type=ENTITY_SENSOR,
            entity_id=sensor.id,
            code="sensor_reassigned",
            message=f"re-dated sensor {sensor.id}'s move to plant {sensor.plant_id} from {redated_from} to {when}",
            severity="info",
            payload={"to_plant_id": sensor.plant_id, "redated_from": redated_from, "effective_at": when},
            timestamp=int(time.time()),
        )

    def list_sensor_assignments(self, sensor_id: int) -> list[SensorAssignment]:
        """All assignment rows for one sensor, oldest first."""
        return list(
            self.session.scalars(
                select(SensorAssignment)
                .where(SensorAssignment.sensor_id == sensor_id)
                .order_by(SensorAssignment.started_at, SensorAssignment.id)
            )
        )

    def judged_since(self, sensor_id: int) -> int:
        """First timestamp at which the sensor's readings count for its current link (0 = all of them).

        After a move (or an unlink) only readings from the boundary plus
        ``SENSOR_MOVE_SETTLE_SECONDS`` on are judged for the sensor's current plant:
        earlier rows were measured in another pot, the settle window is handling noise.
        """
        rows = self.list_sensor_assignments(sensor_id)
        if len(rows) <= 1 and (not rows or rows[0].ended_at is None):
            return 0  # never moved: its first assignment covers all its history
        start = _link_start(rows)
        return 0 if start is None else start + SENSOR_MOVE_SETTLE_SECONDS

    def plant_of_sensor_at(self, sensor_id: int, ts: int) -> int | None:
        """The plant the sensor was measuring at ``ts``, or None when it was unlinked."""
        window = next(
            (w for w in _windows(self.list_sensor_assignments(sensor_id), settle=0) if w.holds(ts)),
            None,
        )
        return window.plant_id if window is not None else None

    def _plant_windows(self, plant_id: int, *, settle: int) -> list[_Window]:
        """Every window in which some sensor measured ``plant_id``."""
        sensor_ids = self.session.scalars(
            select(SensorAssignment.sensor_id).where(SensorAssignment.plant_id == plant_id).distinct()
        )
        return [
            window
            for sensor_id in sorted(sensor_ids)
            for window in _windows(self.list_sensor_assignments(sensor_id), settle=settle)
            if window.plant_id == plant_id
        ]

    def _window_readings(self, window: _Window, since_ts: int, until_ts: int) -> list[SensorReading]:
        """The window's readings inside ``[since_ts, until_ts]``, oldest first."""
        stmt = select(SensorReading).where(
            SensorReading.sensor_id == window.sensor_id,
            SensorReading.timestamp >= max(since_ts, window.starts),
            SensorReading.timestamp <= until_ts,
        )
        if window.ends is not None:
            stmt = stmt.where(SensorReading.timestamp < window.ends)
        return list(self.session.scalars(stmt.order_by(SensorReading.timestamp)))

    def readings_for_plant(self, plant_id: int, *, since_ts: int, until_ts: int | None = None) -> list[SensorReading]:
        """Return SensorReading rows attributed to ``plant_id`` over the window, oldest first.

        A reading is included only when the sensor it came from was assigned to
        this plant at the reading timestamp (raw rows, settle window included:
        this is the display/archive view).
        """
        upper = until_ts if until_ts is not None else int(time.time())
        rows = [r for w in self._plant_windows(plant_id, settle=0) for r in self._window_readings(w, since_ts, upper)]
        return sorted(rows, key=lambda r: (r.timestamp, r.id))

    def judged_readings_for_plant(self, plant_id: int, *, since_ts: int) -> dict[int, list[SensorReading]]:
        """Raw readings attributed to ``plant_id`` per sensor (oldest first), settle windows excluded.

        Per sensor so callers clean each sensor's own timeline before pooling.
        """
        upper = int(time.time())
        by_sensor: dict[int, list[SensorReading]] = {}
        for window in self._plant_windows(plant_id, settle=SENSOR_MOVE_SETTLE_SECONDS):
            by_sensor.setdefault(window.sensor_id, []).extend(self._window_readings(window, since_ts, upper))
        return {sid: sorted(rows, key=lambda r: r.timestamp) for sid, rows in by_sensor.items() if rows}
