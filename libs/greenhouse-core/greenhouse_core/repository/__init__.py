"""Database operations for the irrigation system: the one persistence facade over the ORM.

``IrrigationRepository`` is composed from one mixin per group of entities (the private
submodules), so its public methods, their signatures and this import path are unchanged.
The irrigation-event queries stay in this module: they read this module's ``time``, which
tests rewind to reach their cutoff edges (``tests/test_contract_stats.py``).
"""

import json
import time
from typing import Unpack

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from greenhouse_core.constants import (
    DEFAULT_AUTO_RUN,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_INTERVAL_HOURS,
    DEFAULT_IRRIGATION_MODE,
    SECONDS_PER_HOUR,
)
from greenhouse_core.models import (
    ENTITY_PLANT,
    ENTITY_SENSOR,
    EVENT_ACTION_START,
    ActivityEvent,
    Alert,
    Cluster,
    DecisionLog,
    EventAction,
    GlobalIrrigationConfig,
    IrrigationConfig,
    IrrigationEvent,
    IrrigationWindow,
    Irrigator,
    Plant,
    PlantHealthDaily,
    Sensor,
    SensorAssignment,
    SensorReading,
    TriggeredBy,
    UserPreferences,
    VacationWindow,
)
from greenhouse_core.repository._archive import ArchiveMixin
from greenhouse_core.repository._assignments import AssignmentWindow
from greenhouse_core.repository._irrigation import IrrigatorMixin
from greenhouse_core.repository._settings import SettingsMixin
from greenhouse_core.repository._types import (
    ActivityEventOptions,
    ActivityFilters,
    AlertOptions,
    ClusterPatch,
    DecisionLogEntry,
    DeviceIdExistsError,
    EffectiveConfig,
    EffectiveField,
    EventDetails,
    InvalidMoveTimeError,
    IrrigationConfigPatch,
    IrrigationWindowPatch,
    IrrigatorExistsError,
    IrrigatorPatch,
    NotArchivedError,
    PlantCareFields,
    PlantHealthScores,
    PlantPatch,
    PreferencesPatch,
    ReadingValues,
    SameClusterMoveError,
    SensorPatch,
    SensorPlantLink,
    VacationWindowPatch,
)

# The repository API, plus the names the pinned import surface (tests/golden/contracts/imports.json)
# has always resolved from this module (ORM models, config defaults, the SQL helpers and ``time``).
__all__ = [
    "DEFAULT_AUTO_RUN",
    "DEFAULT_DURATION_MINUTES",
    "DEFAULT_INTERVAL_HOURS",
    "DEFAULT_IRRIGATION_MODE",
    "ENTITY_PLANT",
    "ENTITY_SENSOR",
    "ActivityEvent",
    "ActivityEventOptions",
    "ActivityFilters",
    "Alert",
    "AlertOptions",
    "AssignmentWindow",
    "Cluster",
    "ClusterPatch",
    "DecisionLog",
    "DecisionLogEntry",
    "DeviceIdExistsError",
    "EffectiveConfig",
    "EffectiveField",
    "EventDetails",
    "GlobalIrrigationConfig",
    "InvalidMoveTimeError",
    "IrrigationConfig",
    "IrrigationConfigPatch",
    "IrrigationEvent",
    "IrrigationRepository",
    "IrrigationWindow",
    "IrrigationWindowPatch",
    "Irrigator",
    "IrrigatorExistsError",
    "IrrigatorPatch",
    "NotArchivedError",
    "Plant",
    "PlantCareFields",
    "PlantHealthDaily",
    "PlantHealthScores",
    "PlantPatch",
    "PreferencesPatch",
    "ReadingValues",
    "SameClusterMoveError",
    "Sensor",
    "SensorAssignment",
    "SensorPatch",
    "SensorPlantLink",
    "SensorReading",
    "Session",
    "UserPreferences",
    "VacationWindow",
    "VacationWindowPatch",
    "func",
    "json",
    "select",
    "sqlite_insert",
    "time",
]


class IrrigationRepository(ArchiveMixin, IrrigatorMixin, SettingsMixin):
    """Repository for irrigation system data access."""

    # ── Irrigation Events ─────────────────────────────────────────────────────

    def add_irrigation_event(
        self,
        irrigator_id: int,
        action: EventAction,
        triggered_by: TriggeredBy,
        **details: Unpack[EventDetails],
    ) -> int:
        """Log an irrigation event and return its ID."""
        ts = details.get("timestamp") or int(time.time())
        event = IrrigationEvent(
            irrigator_id=irrigator_id,
            timestamp=ts,
            action=action,
            duration_minutes=details.get("duration_minutes"),
            triggered_by=triggered_by,
            notes=details.get("notes"),
        )
        self.session.add(event)
        self.session.flush()
        return event.id

    def get_recent_events(self, irrigator_id: int, hours: int = 24) -> list[IrrigationEvent]:
        """Get recent events for an irrigator, ordered by timestamp DESC."""
        cutoff = int(time.time()) - (hours * SECONDS_PER_HOUR)
        return list(
            self.session.scalars(
                select(IrrigationEvent)
                .where(IrrigationEvent.irrigator_id == irrigator_id, IrrigationEvent.timestamp >= cutoff)
                .order_by(IrrigationEvent.timestamp.desc())
            )
        )

    def irrigator_consumption_liters(self, irrigator_id: int, since: int, until: int) -> float:
        """Liters dispensed by an irrigator over ``[since, until]`` (inclusive).

        Sums ``duration_minutes`` across ``action == "start"`` events in the
        window and multiplies by the irrigator's ``flow_rate_l_per_min``.
        Returns ``0.0`` when the irrigator is unknown or has no flow rate
        configured (so an uncapped irrigator never spuriously consumes budget).
        """
        irrigator = self.session.get(Irrigator, irrigator_id)
        if irrigator is None or irrigator.flow_rate_l_per_min is None:
            return 0.0
        total_minutes = self.session.scalar(
            select(func.coalesce(func.sum(IrrigationEvent.duration_minutes), 0)).where(
                IrrigationEvent.irrigator_id == irrigator_id,
                IrrigationEvent.action == EVENT_ACTION_START,
                IrrigationEvent.timestamp >= since,
                IrrigationEvent.timestamp <= until,
            )
        )
        return float(total_minutes or 0) * irrigator.flow_rate_l_per_min

    def list_start_events_since(self, irrigator_id: int, since: int) -> list[IrrigationEvent]:
        """An irrigator's ``start`` events at or after ``since``, newest first."""
        return list(
            self.session.scalars(
                select(IrrigationEvent)
                .where(
                    IrrigationEvent.irrigator_id == irrigator_id,
                    IrrigationEvent.action == EVENT_ACTION_START,
                    IrrigationEvent.timestamp >= since,
                )
                .order_by(IrrigationEvent.timestamp.desc())
            )
        )

    def list_events_since(self, irrigator_id: int, since: int) -> list[IrrigationEvent]:
        """Every event of an irrigator at or after ``since``, in database order (callers only aggregate)."""
        return list(
            self.session.scalars(
                select(IrrigationEvent).where(
                    IrrigationEvent.irrigator_id == irrigator_id, IrrigationEvent.timestamp >= since
                )
            )
        )
