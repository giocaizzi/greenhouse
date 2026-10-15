"""Value types of the repository: PATCH payloads, keyword groups, config defaults and domain errors."""

from typing import Any, Literal, Required, TypedDict

from greenhouse_core.constants import (
    DEFAULT_AUTO_RUN,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_INTERVAL_HOURS,
    DEFAULT_IRRIGATION_MODE,
)
from greenhouse_core.models import (
    EntityType,
    TriggeredBy,
)

_GLOBAL_CONFIG_DEFAULTS: dict[str, int | str | bool | None] = {
    "mode": DEFAULT_IRRIGATION_MODE,
    "duration_minutes": DEFAULT_DURATION_MINUTES,
    "interval_hours": DEFAULT_INTERVAL_HOURS,
    "auto_run": DEFAULT_AUTO_RUN,
    "daily_cap_minutes": None,
    "max_events_per_day": None,
    # Quiet hours have no built-in fallback — the production migration seeds
    # the global row with the canonical 00:00-05:00 window, and fresh-DB flows
    # (tests, dev installs) start with quiet hours disabled until explicitly
    # configured. See ``get_global_irrigation_config``.
    "quiet_start_hour": None,
    "quiet_end_hour": None,
}


class EffectiveField(TypedDict):
    """One resolved irrigation-config field: its value and the layer it came from."""

    value: int | str | bool | None
    source: Literal["cluster", "global", "default"]


# ``get_effective_config`` result: one ``EffectiveField`` per patchable config field.
EffectiveConfig = dict[str, EffectiveField]


# ── PATCH payloads for the update_* methods ──────────────────────────────────
# Keys a caller may pass (all optional). Runtime semantics are the methods' own:
# ``None`` skips a field (except the config upserts, where ``None`` clears it) and
# keys that are not attributes of the row are ignored.


class ClusterPatch(TypedDict, total=False):
    """Fields ``update_cluster`` may change."""

    name: str | None
    location: str | None
    environment: str | None


class PlantPatch(TypedDict, total=False):
    """Fields ``update_plant`` may change."""

    species: str | None
    category: str | None
    water_needs: str | None
    light_needs: str | None
    ideal_temp_min: float | None
    ideal_temp_max: float | None
    ideal_humidity_min: float | None
    ideal_humidity_max: float | None
    notes: str | None


class SensorPatch(TypedDict, total=False):
    """Fields ``update_sensor`` may change (``plant_id`` goes through the assignment history)."""

    name: str | None
    type: str | None
    config: dict[Any, Any] | None
    plant_id: int | None


class IrrigatorPatch(TypedDict, total=False):
    """Fields ``update_irrigator`` may change."""

    name: str | None
    type: str | None
    config: dict[Any, Any] | None
    reservoir_l: float | None
    flow_rate_l_per_min: float | None


class VacationWindowPatch(TypedDict, total=False):
    """Fields ``update_vacation_window`` may change."""

    starts_at: int | None
    ends_at: int | None
    contact_email: str | None
    notes: str | None


class IrrigationWindowPatch(TypedDict, total=False):
    """Fields ``update_irrigation_window`` may change."""

    start_hour: int | None
    end_hour: int | None
    weekday_mask: int | None
    label: str | None


class PreferencesPatch(TypedDict, total=False):
    """Fields ``update_preferences`` may change."""

    units: str | None
    timezone: str | None
    theme: str | None
    default_cluster_id: int | None
    refresh_interval_seconds: int | None
    dry_run_global: bool | None
    notify_manual: bool | None
    notify_emergency: bool | None
    notify_alerts: bool | None
    notify_auto: bool | None
    scheduler_paused: bool | None


class IrrigationConfigPatch(TypedDict, total=False):
    """Fields ``set_irrigation_config`` / ``update_global_irrigation_config`` may set (``None`` clears)."""

    mode: str | None
    duration_minutes: int | None
    interval_hours: int | None
    auto_run: bool | None
    daily_cap_minutes: int | None
    max_events_per_day: int | None
    quiet_start_hour: int | None
    quiet_end_hour: int | None


# ── Optional keyword groups of the add_* / upsert_* writers ─────────────────
# Spelled once as ``**fields: Unpack[...]`` so a write method's signature stays short while
# every call site keeps its keywords; a key left out takes the default documented per field.


class PlantCareFields(TypedDict, total=False):
    """Optional care data ``add_plant`` stores (each defaults to ``None``)."""

    category: str | None
    water_needs: str | None
    light_needs: str | None
    ideal_temp_min: float | None
    ideal_temp_max: float | None
    ideal_humidity_min: float | None
    ideal_humidity_max: float | None
    notes: str | None


class SensorPlantLink(TypedDict, total=False):
    """``add_sensor``'s optional plant link: ``plant_id`` and when its assignment starts (default: now)."""

    plant_id: int | None
    assignment_started_at: int | None


class ReadingValues(TypedDict, total=False):
    """One reading's values for ``add_sensor_reading`` (each defaults to ``None``; ``timestamp`` to now)."""

    timestamp: int | None
    temperature: float | None
    soil_moisture: float | None
    light: int | None
    env_humidity: float | None
    battery_state: str | None
    water_warning: bool | None


class EventDetails(TypedDict, total=False):
    """``add_irrigation_event``'s optional details (``None`` each; ``timestamp`` defaults to now)."""

    duration_minutes: int | None
    notes: str | None
    timestamp: int | None


class DecisionLogEntry(TypedDict, total=False):
    """The columns of one ``decision_logs`` row (``triggered_by`` defaults to auto, ``actuated`` to False)."""

    evaluated_at: Required[int]
    action: Required[str]
    duration_minutes: Required[int]
    interval_hours: Required[int]
    confidence: Required[float]
    primary_code: Required[str | None]
    reason_text: Required[str]
    payload: Required[dict[str, Any]]
    triggered_by: TriggeredBy
    actuated: bool


class ActivityEventOptions(TypedDict, total=False):
    """``add_activity_event``'s optional fields (``severity`` defaults to ``"info"``, ``timestamp`` to now)."""

    entity_id: int | None
    severity: str
    payload: dict[str, Any] | None
    timestamp: int | None


class ActivityFilters(TypedDict, total=False):
    """``list_activity_events`` filters (``None`` = no filter) and paging (``limit`` 100, ``before`` cursor)."""

    entity_type: str | None
    entity_id: int | None
    source: str | None
    severity: str | None
    limit: int
    before: int | None


class AlertOptions(TypedDict, total=False):
    """``upsert_alert``'s optional fields (``severity`` "info", ``entity_type`` cluster, ``seen_at`` now)."""

    severity: str
    entity_type: EntityType
    entity_id: int | None
    cluster_id: int | None
    plant_id: int | None
    payload: dict[str, Any] | None
    seen_at: int | None


class PlantHealthScores(TypedDict, total=False):
    """``upsert_plant_health``'s sub-scores and ``sensor_ids`` (``None`` each), ``sample_count`` (0), ``timestamp``."""

    soil_in_band_pct: float | None
    temp_in_band_pct: float | None
    humidity_in_band_pct: float | None
    efficiency: float | None
    sample_count: int
    timestamp: int | None
    sensor_ids: list[int] | None


class InvalidMoveTimeError(ValueError):
    """A sensor move's ``effective_at`` would precede the current link or overlap the previous assignment."""


class NotArchivedError(ValueError):
    """A purge was asked for a sensor or plant that is not archived (archive comes first)."""


class SameClusterMoveError(ValueError):
    """Raised when a plant move targets its current cluster (no-op move)."""


class DeviceIdExistsError(LookupError):
    """The Tuya device id is already registered (the database's unique constraint refused it)."""


class IrrigatorExistsError(ValueError):
    """Raised when adding an irrigator to a cluster that already has one.

    A cluster has at most one irrigator (strict 0:1). The API layer maps this
    to HTTP 409 Conflict.
    """
