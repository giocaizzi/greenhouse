"""Data-driven golden decision grid for :class:`IrrigationLogic` (Phase 1 characterization).

Each :class:`Case` is a frozen, declarative description of one engine evaluation:
an instant, the preferences timezone, a cluster (plants, sensors + reading series,
irrigator, events, quiet hours, windows, vacation, leak alert, weather forecast,
learning history) and the call arguments. :func:`run_case` seeds a fresh
in-memory SQLite database through the real :class:`IrrigationRepository`, calls
the public entry point ``IrrigationLogic.decide_for_cluster(..., persist=True)``
under ``time_machine.travel(case.at, tick=False)`` and returns a JSON-able record
of the decision **and** the persisted ``decision_logs`` row(s).

The module is deliberately free of pytest so the Phase 5 differential test can
reuse it verbatim::

    from engine_grid import build_cases, run_case
    for case in build_cases():
        assert run_case(case) == run_case(case, engine_factory=ReferenceEngine)

Every value is derived from ``case.at`` — nothing reads the real clock outside
the frozen block — so records are reproducible bit-for-bit.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict, Unpack, cast
from zoneinfo import ZoneInfo

import time_machine
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from greenhouse_core.constants import LEAK_ALERT_CODE
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.models import (
    EVENT_ACTION_START,
    TRIGGERED_BY_AUTO,
    ActivitySource,
    Base,
    DecisionLog,
    EntityType,
    IrrigationEvent,
    SensorReading,
)
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy import Engine

    from greenhouse_core.models import EventAction, TriggeredBy

MINUTE = 60
HOUR = 3600
DAY = 86400

ROME = "Europe/Rome"

# ── Case model ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reading:
    """One sensor reading, timestamped ``age_s`` seconds before the case instant."""

    age_s: int
    soil: float | None = None
    temp: float | None = None
    hum: float | None = None
    light: int | None = None
    water_warning: bool | None = None


@dataclass(frozen=True)
class SensorSpec:
    """A soil sensor; ``plant`` indexes ``Case.plants`` (``None`` = unlinked)."""

    name: str
    readings: tuple[Reading, ...]
    plant: int | None = None
    moved: tuple[int, int] | None = None  # (old plant index, move age_s): on that plant until the move


@dataclass(frozen=True)
class Event:
    """An ``IrrigationEvent`` row ``age_s`` seconds before the case instant."""

    age_s: int
    action: str = "start"
    triggered_by: str = "auto"
    duration: int | None = 2


@dataclass(frozen=True)
class Leak:
    """A ``leak_or_stuck_valve`` alert last seen ``age_s`` seconds ago."""

    status: str = "open"  # open | acknowledged | resolved
    age_s: int = HOUR
    other_cluster: bool = False


@dataclass(frozen=True)
class Window:
    """An ``IrrigationWindow`` row (local hours, end-exclusive; Mon=bit 0)."""

    start: int
    end: int
    mask: int = 127


@dataclass(frozen=True)
class Case:
    """One declarative engine evaluation. See module docstring."""

    id: str
    family: str
    at: datetime
    tz: str | None = None  # UserPreferences.timezone (None = row default "UTC")
    environment: str = "indoor"
    plants: tuple[tuple[str, str | None], ...] = (("Monstera deliciosa", "tropical"),)
    sensors: tuple[SensorSpec, ...] = ()
    irrigator: bool = True
    reservoir_l: float | None = None
    flow_rate: float | None = None
    events: tuple[Event, ...] = ()
    global_quiet: tuple[int, int] | None = None
    cluster_quiet: tuple[int, int] | None = None
    cluster_config: tuple[tuple[str, Any], ...] = ()
    windows: tuple[Window, ...] = ()
    vacation: tuple[int, int] | None = None  # (starts_at, ends_at) offsets from `at`, seconds
    leak: Leak | None = None
    weather: str = "none"  # none (no client) | offline (get_forecast → None) | forecast
    forecast: tuple[tuple[str, Any], ...] = ()
    current_temp: float | None = None
    bypass_quiet_hours: bool = False
    triggered_by: str = "auto"
    learning: str | None = None  # history seed: blocked_drip | rapid_drainage
    insert_desc: bool = False  # insert readings newest-first (ordering pitfall)
    cluster_exists: bool = True


class CaseOverrides(TypedDict, total=False):
    """The optional :class:`Case` fields, for the ``**kw`` dicts a family spreads into ``Case(...)``."""

    tz: str | None
    environment: str
    plants: tuple[tuple[str, str | None], ...]
    sensors: tuple[SensorSpec, ...]
    irrigator: bool
    reservoir_l: float | None
    flow_rate: float | None
    events: tuple[Event, ...]
    global_quiet: tuple[int, int] | None
    cluster_quiet: tuple[int, int] | None
    cluster_config: tuple[tuple[str, Any], ...]
    windows: tuple[Window, ...]
    vacation: tuple[int, int] | None
    leak: Leak | None
    weather: str
    forecast: tuple[tuple[str, Any], ...]
    current_temp: float | None
    bypass_quiet_hours: bool
    triggered_by: str
    learning: str | None
    insert_desc: bool
    cluster_exists: bool


class ScheduleSetup(TypedDict, total=False):
    """Quiet-hour / window overrides the quiet and DST families spread into ``Case(...)``."""

    global_quiet: tuple[int, int]
    cluster_quiet: tuple[int, int]
    windows: tuple[Window, ...]


class SensorScene(TypedDict, total=False):
    """The probes (or none, plus the fallback temperature) a scene is judged on."""

    sensors: tuple[SensorSpec, ...]
    current_temp: float | None


class SceneSetup(SensorScene, total=False):
    """Scene overrides (plants, history, environment) spread into ``Case(...)`` beside explicit fields."""

    plants: tuple[tuple[str, str | None], ...]
    events: tuple[Event, ...]
    environment: str
    bypass_quiet_hours: bool


class VacationSetup(TypedDict, total=False):
    """Vacation-family overrides: the window, the reservoir budget and the spend so far."""

    vacation: tuple[int, int]
    reservoir_l: float
    flow_rate: float
    events: tuple[Event, ...]
    irrigator: bool


class WeatherSetup(TypedDict, total=False):
    """Weather-family overrides: which client is wired and what it forecasts."""

    weather: str
    forecast: tuple[tuple[str, Any], ...]


class QuietExtraSetup(SceneSetup, WeatherSetup, total=False):
    """Quiet-family extras: any scene plus a weather client."""


class ClimateFields(TypedDict, total=False):
    """The non-soil :class:`Reading` fields a sensor helper passes through."""

    temp: float | None
    hum: float | None
    light: int | None
    water_warning: bool | None


class ReadingFields(ClimateFields, total=False):
    """Every optional :class:`Reading` field."""

    soil: float | None


# One ``bulk_add_sensor_readings`` row: (sensor_id, timestamp, temperature, soil_moisture, light,
# env_humidity, battery_state, water_warning).
# (year, month, day, hour, minute, second) for the case-instant helpers.
_YMDHMS = tuple[int, int, int, int, int, int]

ReadingRow = tuple[int, int, float | None, float | None, int | None, float | None, str | None, int | None]


# ── Weather stubs (deterministic, no network) ────────────────────────────────


class FakeForecastWeather:
    """``WeatherClient`` stand-in returning a fixed forecast dict (or ``None``)."""

    def __init__(self, forecast: dict[str, Any] | None) -> None:
        """Serve ``forecast`` (None for no forecast) and record each request."""
        self._forecast = forecast
        self.forecast_calls: list[int] = []

    def get_forecast(self, hours: int = 6) -> dict[str, Any] | None:
        """Record the requested horizon and return a copy of the canned forecast."""
        self.forecast_calls.append(hours)
        return None if self._forecast is None else dict(self._forecast)

    def get_current(self) -> None:
        """Return no current weather; the grid only drives the forecast."""


def make_weather(case: Case) -> FakeForecastWeather | None:
    """Build the weather client the case asks for (``None`` = no client wired)."""
    if case.weather == "none":
        return None
    if case.weather == "offline":
        return FakeForecastWeather(None)
    return FakeForecastWeather(dict(case.forecast))


# ── DB plumbing ──────────────────────────────────────────────────────────────

_PLANT_DB_PATH = Path(str(files("greenhouse_core") / "data" / "plant_database.json"))


@functools.cache
def plant_database() -> PlantDatabase:
    """The bundled plant DB, loaded from the package path (ignores ``IRRIGATION_PLANT_DB_PATH``).

    Loaded once per process and shared.
    """
    return PlantDatabase(_PLANT_DB_PATH)


@functools.cache
def _shared_engine() -> Engine:
    """The one in-memory engine (schema created once) that every grid case runs on, per process."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def fresh_repository() -> Iterator[IrrigationRepository]:
    """Yield a repository on an empty schema; everything is rolled back afterwards.

    One in-memory engine (``create_all``, like ``tests/conftest.py::tmp_db``) is
    shared per process; each case runs in an outer transaction that is rolled
    back, so ids restart at 1 and no state leaks between cases.
    """
    conn = _shared_engine().connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield IrrigationRepository(session)
    finally:
        session.close()
        trans.rollback()
        conn.close()


def _seed(repo: IrrigationRepository, case: Case) -> int:
    now = int(case.at.timestamp())
    _seed_preferences(repo, case)
    cluster_id = repo.add_cluster(f"Grid {case.family}", environment=case.environment)
    plant_ids = [repo.add_plant(cluster_id=cluster_id, species=s, category=c) for s, c in case.plants]
    _seed_cluster_config(repo, case, cluster_id)
    irrigator_id = _seed_irrigator(repo, case, cluster_id)
    sensor_ids = _seed_sensors(repo, case, cluster_id, plant_ids, now)
    for ev in case.events:
        assert irrigator_id is not None, "a case with events needs an irrigator"
        repo.add_irrigation_event(
            irrigator_id=irrigator_id,
            # The grid seeds actions outside the enum on purpose (``schedule_updated``, issue #103).
            action=cast("EventAction", ev.action),
            triggered_by=cast("TriggeredBy", ev.triggered_by),
            duration_minutes=ev.duration,
            timestamp=now - ev.age_s,
        )

    if case.learning is not None:
        assert irrigator_id is not None, "a learning seed needs an irrigator"
        _seed_learning(repo, case.learning, irrigator_id, sensor_ids, now)

    for w in case.windows:
        repo.add_irrigation_window(cluster_id, start_hour=w.start, end_hour=w.end, weekday_mask=w.mask)

    if case.vacation is not None:
        repo.add_vacation_window(starts_at=now + case.vacation[0], ends_at=now + case.vacation[1])

    if case.leak is not None:
        _seed_leak(repo, case.leak, cluster_id, sensor_ids, now)

    repo.session.flush()
    return cluster_id


def _seed_preferences(repo: IrrigationRepository, case: Case) -> None:
    if case.tz is not None:
        repo.update_preferences(timezone=case.tz)
    if case.global_quiet is not None:
        repo.update_global_irrigation_config(quiet_start_hour=case.global_quiet[0], quiet_end_hour=case.global_quiet[1])


def _seed_cluster_config(repo: IrrigationRepository, case: Case, cluster_id: int) -> None:
    config = dict(case.cluster_config)
    if case.cluster_quiet is not None:
        config["quiet_start_hour"], config["quiet_end_hour"] = case.cluster_quiet
    if config:
        repo.set_irrigation_config(cluster_id, **config)


def _seed_irrigator(repo: IrrigationRepository, case: Case, cluster_id: int) -> int | None:
    if not case.irrigator:
        return None
    irrigator_id = repo.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id="fake_tuya_device_grid0001",
        name="Grid Pump",
        irrigator_type="rainpoint.ik10pw",
        config={},
    )
    if case.reservoir_l is not None or case.flow_rate is not None:
        irr = repo.get_irrigator(irrigator_id)
        assert irr is not None
        irr.reservoir_l = case.reservoir_l
        irr.flow_rate_l_per_min = case.flow_rate
        repo.session.flush()
    return irrigator_id


def _seed_sensors(repo: IrrigationRepository, case: Case, cluster_id: int, plant_ids: list[int], now: int) -> list[int]:
    sensor_ids: list[int] = []
    for i, spec in enumerate(case.sensors):
        sid = repo.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=f"fake_tuya_sensor_grid{i:04d}",
            name=spec.name,
            sensor_type="tuya.tr301z",
            config={},
            plant_id=plant_ids[spec.moved[0] if spec.moved else spec.plant] if spec.plant is not None else None,
            assignment_started_at=0,
        )
        if spec.moved is not None and spec.plant is not None:
            repo.reassign_sensor_to_plant(sid, plant_ids[spec.plant], when=now - spec.moved[1])
        sensor_ids.append(sid)
        rows: list[ReadingRow] = [
            (sid, now - r.age_s, r.temp, r.soil, r.light, r.hum, None, r.water_warning) for r in spec.readings
        ]
        rows.sort(key=lambda row: row[1], reverse=case.insert_desc)
        repo.bulk_add_sensor_readings(rows)
    return sensor_ids


def _seed_leak(repo: IrrigationRepository, leak: Leak, cluster_id: int, sensor_ids: list[int], now: int) -> None:
    leak_cluster = repo.add_cluster("Grid other") if leak.other_cluster else cluster_id
    alert = repo.upsert_alert(
        dedup_key=f"leak::{LEAK_ALERT_CODE}::{leak_cluster}::sensor1",
        source=ActivitySource.LEAK,
        code=LEAK_ALERT_CODE,
        title="Possible leak or stuck valve detected",
        message="Grid Soil: soil moisture still rising after irrigation (latest=78.0%)",
        severity="critical",
        entity_type=EntityType.SENSOR,
        entity_id=sensor_ids[0] if sensor_ids else None,
        cluster_id=leak_cluster,
        seen_at=now - leak.age_s,
    )
    if leak.status == "acknowledged":
        repo.acknowledge_alert(alert.id)
    elif leak.status == "resolved":
        repo.resolve_alert(alert.id)


def _seed_learning(repo: IrrigationRepository, seed: str, irrigator_id: int, sensor_ids: list[int], now: int) -> None:
    """Seed three past irrigation cycles that make the learner raise alerts."""
    pattern_sets: dict[str, list[tuple[tuple[int, float], ...]]] = {
        "blocked_drip": [((-600, 40.0), (1200, 40.5), (3600, 40.4))],
        "rapid_drainage": [((-600, 40.0), (1200, 70.0), (2 * HOUR, 62.0), (3 * HOUR, 54.0), (4 * HOUR, 46.0))],
        # sensor 0 dry + absorbs fast, sensor 1 wet → unresolvable conflict
        "conflict": [((-600, 20.0), (1200, 30.0)), ((-600, 78.0), (1200, 84.0))],
    }
    patterns = pattern_sets[seed]
    rows: list[ReadingRow] = []
    for k in (1, 2, 3):
        ev = now - k * DAY - 6 * HOUR
        repo.add_irrigation_event(
            irrigator_id=irrigator_id,
            action=EVENT_ACTION_START,
            triggered_by=TRIGGERED_BY_AUTO,
            duration_minutes=2,
            timestamp=ev,
        )
        for index, sensor_id in enumerate(sensor_ids[: len(patterns)]):
            rows += [(sensor_id, ev + offset, None, soil, None, None, None, None) for offset, soil in patterns[index]]
    repo.bulk_add_sensor_readings(rows)


# ── Running a case ───────────────────────────────────────────────────────────


def _readings_fingerprint(repo: IrrigationRepository) -> list[tuple[object, ...]]:
    rows = repo.session.execute(
        select(
            SensorReading.id,
            SensorReading.sensor_id,
            SensorReading.timestamp,
            SensorReading.temperature,
            SensorReading.soil_moisture,
            SensorReading.light,
            SensorReading.env_humidity,
            SensorReading.water_warning,
        ).order_by(SensorReading.id)
    )
    return [tuple(r) for r in rows]


def _log_record(row: DecisionLog, decision_dump: dict[str, Any] | None) -> dict[str, object]:
    payload = json.loads(row.payload_json)
    return {
        "id": row.id,
        "cluster_id": row.cluster_id,
        "evaluated_at": row.evaluated_at,
        "action": row.action,
        "duration_minutes": row.duration_minutes,
        "interval_hours": row.interval_hours,
        "confidence": row.confidence,
        "primary_code": row.primary_code,
        "reason_text": row.reason_text,
        "triggered_by": row.triggered_by,
        "actuated": row.actuated,
        # The payload is the decision dump; pin byte-exact serialization via a hash
        # and its content via equality (the dump itself is pinned under "decision").
        "payload_json_sha256": hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest(),
        "payload_equals_decision": payload == decision_dump,
    }


def run_case(case: Case, *, engine_factory: Callable[..., Any] = IrrigationLogic) -> dict[str, Any]:
    """Evaluate ``case`` and return its JSON-able golden record."""
    with time_machine.travel(case.at, tick=False), fresh_repository() as repo:
        cluster_id = _seed(repo, case)
        readings_before = _readings_fingerprint(repo)
        events_before = repo.session.execute(select(func.count()).select_from(IrrigationEvent)).scalar_one()
        logic = engine_factory(repo, plant_database(), weather_client=make_weather(case))
        target = cluster_id if case.cluster_exists else cluster_id + 1000
        decision = logic.decide_for_cluster(
            target,
            case.current_temp,
            persist=True,
            triggered_by=case.triggered_by,
            bypass_quiet_hours=case.bypass_quiet_hours,
        )
        dump = decision.model_dump(mode="json") if decision is not None else None
        logs = list(repo.session.scalars(select(DecisionLog).order_by(DecisionLog.id)))
        events_after = repo.session.execute(select(func.count()).select_from(IrrigationEvent)).scalar_one()
        return {
            "decision": dump,
            "decision_log_id_matches": (
                decision.decision_log_id == (logs[0].id if logs else None) if decision is not None else None
            ),
            "decision_logs": [_log_record(row, dump) for row in logs],
            "irrigation_events_written": events_after - events_before,
            "sensor_readings_unchanged": _readings_fingerprint(repo) == readings_before,
        }


# ── Grid ─────────────────────────────────────────────────────────────────────


def _utc(*args: *_YMDHMS) -> datetime:
    return datetime(*args, tzinfo=ZoneInfo("UTC"))


def _local(tz: str, *args: *_YMDHMS) -> datetime:
    return datetime(*args, tzinfo=ZoneInfo(tz))


# Wed 2026-04-15 10:00 UTC — mirrors golden.FROZEN_INSTANT (spring, mid-morning).
BASE_AT = _utc(2026, 4, 15, 10, 0, 0)

PLANT_SETS: dict[str, tuple[tuple[str, str | None], ...]] = {
    # tropical category-level timing (_category_defaults.tropical), species without timing fields
    "monstera": (("Monstera deliciosa", "tropical"),),
    # species-level season_frequency_multiplier override
    "alocasia": (("Alocasia amazonica", "tropical"),),
    # species-level multiplier, low water needs, target 30-50
    "dracaena": (("Dracaena marginata", "tropical"),),
    # species-level *_outdoor multiplier only (indoor falls to category)
    "loquat": (("Eriobotrya japonica", "fruit_tree"),),
    # unknown species, category with both indoor and _outdoor multipliers
    "fruit_cat": (("Unknown fruit tree", "fruit_tree"),),
    # unknown species, fern category: high water needs, no _outdoor key
    "fern_cat": (("Unknown fern", "fern"),),
    # unknown species, cacti category: low water needs, target 15-35
    "cactus_cat": (("Unknown cactus", "cacti"),),
    # unknown species, no category: built-in defaults only
    "unknown": (("Plantus fictitius", None),),
}

MIXED_PLANTS = (
    ("Dracaena marginata", "tropical"),
    ("Monstera deliciosa", "tropical"),
    ("Unknown fern", "fern"),
)

# Multi-plant clusters: one representative plant drives the seasonal multiplier
# (first plant by species name with a species / category override).
SEASON_EXTRA_SETS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "mixed": MIXED_PLANTS,
    "unknown+loquat": (("Abies incognita", None), ("Eriobotrya japonica", "fruit_tree")),
    "loquat+unknown": (("Eriobotrya japonica", "fruit_tree"), ("Zamia incognita", None)),
}

CLIMATES: dict[str, ClimateFields] = {
    "soil_only": {},
    "hot_dry_bright": {"temp": 34.0, "hum": 25.0, "light": 2000},
    "cold_humid_dark": {"temp": 10.0, "hum": 90.0, "light": 30},
}


def _sensor(
    soil: float | None,
    *,
    name: str = "Grid Soil",
    age_s: int = 600,
    plant: int | None = 0,
    **kw: Unpack[ClimateFields],
) -> SensorSpec:
    return SensorSpec(name=name, readings=(Reading(age_s=age_s, soil=soil, **kw),), plant=plant)


def _series(
    values: list[float],
    *,
    step_s: int = HOUR,
    name: str = "Grid Soil",
    field: Literal["soil", "temp"] = "soil",
    **const: Unpack[ReadingFields],
) -> SensorSpec:
    """Hourly series ending at age 0; ``values`` are oldest → newest."""
    n = len(values)
    readings = tuple(_series_reading((n - 1 - i) * step_s, field, v, const) for i, v in enumerate(values))
    return SensorSpec(name=name, readings=readings, plant=0)


def _series_reading(age_s: int, field: Literal["soil", "temp"], value: float, const: ReadingFields) -> Reading:
    """One series point: ``value`` lands in ``field``, the constant fields ride along."""
    fields: ReadingFields = {**const}
    fields[field] = value
    return Reading(age_s=age_s, **fields)


def _soil_family() -> list[Case]:
    cases: list[Case] = []
    levels = [0.0, 12.0, 25.0, 29.9, 30.0, 34.0, 38.0, 44.9, 45.0, 58.0, 66.0, 71.0]
    for plant_key, plants in PLANT_SETS.items():
        cases.extend(
            Case(
                id=f"soil/{plant_key}/{climate_key}/indoor/m{level:g}",
                family="soil",
                at=BASE_AT,
                plants=plants,
                sensors=(_sensor(level, **climate),),
            )
            for climate_key, climate in CLIMATES.items()
            for level in levels
        )
        cases.extend(
            Case(
                id=f"soil/{plant_key}/soil_only/outdoor/m{level:g}",
                family="soil",
                at=BASE_AT,
                environment="outdoor",
                plants=plants,
                sensors=(_sensor(level),),
            )
            for level in (25.0, 38.0, 58.0, 71.0)
        )
    return cases


def _driest_family() -> list[Case]:
    cases: list[Case] = []
    spreads = [
        (20, 55, 58),
        (44, 56),
        (44, 61),
        (40, 62),
        (34, 50, 50),
        (55, 55, 25),
        (10, 70),
        (29, 31),
        (60, 61, 62),
        (25, 25, 80),
        (36, 36, 36),
        (50, 74, 76),
    ]
    for spread in spreads:
        tag = "-".join(str(v) for v in spread)
        mono = tuple(_sensor(float(v), name=f"Soil {chr(65 + i)}", plant=None, temp=22.0) for i, v in enumerate(spread))
        cases.append(Case(id=f"driest/monstera-unlinked/{tag}", family="driest", at=BASE_AT, sensors=mono))
        mixed = tuple(
            _sensor(float(v), name=f"Soil {chr(65 + i)}", plant=i % len(MIXED_PLANTS), temp=22.0)
            for i, v in enumerate(spread)
        )
        cases.append(
            Case(id=f"driest/mixed-linked/{tag}", family="driest", at=BASE_AT, plants=MIXED_PLANTS, sensors=mixed)
        )
    # Driest plant drives the call even when it is the only dry one among wet plants.
    cases.append(
        Case(
            id="driest/one-dry-among-four-wet",
            family="driest",
            at=BASE_AT,
            sensors=tuple(
                _sensor(v, name=f"Soil {chr(65 + i)}", plant=None) for i, v in enumerate((56.0, 57.0, 58.0, 59.0, 33.0))
            ),
        )
    )
    return cases


def _second_review_cases() -> list[Case]:
    """Second-review probes: a glitch beside a level change; critical stress judged on the current state."""
    half_hour = HOUR // 2
    glitch = _series([62.0, 61.0, 60.0, 60.0, 59.0, 58.0, 0.0, 40.0, 40.0], step_s=half_hour)
    dry_then_drowned = _series([25.0] * 4 + [85.0] * 20, step_s=half_hour)
    recovered = _series([25.0] * 6 + [60.0] * 10, step_s=HOUR)
    stuck_low = (
        SensorSpec("Stuck Probe", tuple(Reading(age_s=i * HOUR, soil=3.0) for i in range(6)), None),
        SensorSpec("Wet Probe", tuple(Reading(age_s=i * HOUR, soil=75.0) for i in range(6)), None),
    )
    return [
        Case(id="series/glitch-beside-level-change/monstera", family="series", at=BASE_AT, sensors=(glitch,)),
        Case(
            id="series/glitch-beside-level-change/dracaena",
            family="series",
            at=BASE_AT,
            plants=PLANT_SETS["dracaena"],
            sensors=(glitch,),
        ),
        Case(
            id="series/drowning-after-old-dry-period",
            family="series",
            at=BASE_AT,
            sensors=(dry_then_drowned,),
            events=(Event(age_s=7 * HOUR, duration=2),),
        ),
        Case(
            id="series/recovered-after-watering",
            family="series",
            at=BASE_AT,
            sensors=(recovered,),
            events=(Event(age_s=10 * HOUR, duration=2),),
        ),
        Case(id="series/stuck-low-probe-beside-wet-plant", family="series", at=BASE_AT, sensors=stuck_low),
        # A cactus in the cluster must not switch off the Monstera's emergency (per-sensor bands).
        Case(
            id="series/cactus-beside-dry-monstera",
            family="series",
            at=BASE_AT,
            plants=(("Monstera deliciosa", "tropical"), ("Unknown cactus", "cacti")),
            sensors=(
                SensorSpec("Monstera Probe", (Reading(600, soil=16.0),), 0),
                SensorSpec("Cactus Probe", (Reading(600, soil=25.0),), 1),
            ),
        ),
        # Stuck-high probe beside a dry plant whose own probe raises the alarm: not a veto — conflict burst.
        Case(
            id="series/stuck-high-probe-beside-alarming-dry-plant",
            family="series",
            at=BASE_AT,
            sensors=(
                SensorSpec("Stuck High", (Reading(600, soil=90.0),), None),
                SensorSpec("Dry Probe", (Reading(600, soil=20.0, water_warning=True),), 0),
            ),
        ),
        # The alarming probe itself reads wet (faulty high probe, flag stuck on) beside a dry plant: conflict burst.
        Case(
            id="series/wet-alarming-probe-beside-dry-plant",
            family="series",
            at=BASE_AT,
            sensors=(
                SensorSpec("Wet Alarming", (Reading(600, soil=85.0, water_warning=True),), None),
                SensorSpec("Dry Probe", (Reading(600, soil=10.0),), 0),
            ),
        ),
        # A linked cactus above its own band (45 % > 35 %) beside a Monstera at 30 %: per-sensor wetness → conflict.
        Case(
            id="series/wet-cactus-beside-dry-monstera",
            family="series",
            at=BASE_AT,
            plants=(("Monstera deliciosa", "tropical"), ("Unknown cactus", "cacti")),
            sensors=(
                SensorSpec("Monstera Probe", (Reading(600, soil=30.0),), 0),
                SensorSpec("Cactus Probe", (Reading(600, soil=45.0),), 1),
            ),
        ),
        # Device soil-dry alarm while the probe reads 80 % (Monstera band tops at 60 %): noted, not an emergency.
        Case(
            id="series/water-warning-on-over-watered-soil",
            family="series",
            at=BASE_AT,
            sensors=(SensorSpec("Grid Soil", (Reading(600, soil=80.0, water_warning=True),), 0),),
        ),
    ]


def _series_family() -> list[Case]:
    flat_spike_mid = [55.0] * 4 + [10.0] + [55.0] * 4
    shapes: dict[str, tuple[SensorSpec, ...]] = {
        "spike-low-mid": (_series(flat_spike_mid),),
        "spike-low-newest": (_series([55.0] * 8 + [10.0]),),
        "spike-low-oldest": (_series([10.0] + [55.0] * 8),),
        "spike-high-among-dry": (_series([36.0] * 4 + [99.0] + [36.0] * 4),),
        "spike-too-few-to-judge": (_series([55.0, 55.0, 10.0, 55.0]),),
        "out-of-range-soil": (_series([150.0, 50.0, -5.0, 50.0, 50.0]),),
        "out-of-range-temp": (_series([22.0, 22.0, 120.0, 22.0], field="temp", soil=50.0),),
        "declining": (
            _series([60.0, 58.0, 56.0, 54.0, 52.0, 50.0, 48.0, 46.0, 44.0, 42.0, 40.0, 38.0], step_s=2 * HOUR),
        ),
        "steep-decline-low": (_series([58.0, 56.0, 54.0, 50.0, 46.0, 40.0, 36.0, 34.0, 33.0, 32.0], step_s=2 * HOUR),),
        "rising": (_series([30.0, 33.0, 36.0, 39.0, 42.0, 45.0, 48.0, 51.0, 54.0, 57.0], step_s=2 * HOUR),),
        "rising-saturated": (_series([60.0, 64.0, 68.0, 72.0, 76.0, 80.0, 82.0, 84.0], step_s=2 * HOUR),),
        "temp-rising-hot": (_series([22.0, 23.0, 25.0, 27.0, 29.0, 30.0, 31.0, 32.0], field="temp", soil=50.0),),
        "temp-falling": (_series([30.0, 28.0, 26.0, 24.0, 22.0, 20.0], field="temp", soil=50.0),),
        "zero-soil-genuine-drydown": (_series([20.0, 15.0, 10.0, 5.0, 2.0, 0.0]),),
        "light-at-15-ignored": (SensorSpec("Grid Soil", (Reading(600, soil=50.0, light=15),), 0),),
        "light-at-16-counted": (SensorSpec("Grid Soil", (Reading(600, soil=50.0, light=16),), 0),),
        "light-dark-100": (SensorSpec("Grid Soil", (Reading(600, soil=50.0, light=100),), 0),),
        "light-moderate-400": (SensorSpec("Grid Soil", (Reading(600, soil=50.0, light=400),), 0),),
        "gentle-decline-low": (_series([42.0, 40.4, 38.9, 37.3, 35.8, 34.2, 32.7, 31.1, 29.6, 28.0], step_s=2 * HOUR),),
        "temp-rising-very-hot": (_series([31.0, 32.0, 33.0, 34.0, 36.0, 37.0, 38.0, 39.0], field="temp", soil=50.0),),
        "light-bright-1000": (SensorSpec("Grid Soil", (Reading(600, soil=50.0, light=1000),), 0),),
        "water-warning-flag": (SensorSpec("Grid Soil", (Reading(600, soil=50.0, water_warning=True),), 0),),
        "water-warning-old-in-window": (
            SensorSpec("Grid Soil", (Reading(20 * HOUR, soil=50.0, water_warning=True), Reading(600, soil=50.0)), 0),
        ),
        "heat-stress-advisory": (_sensor(50.0, temp=36.0),),
        "humidity-only": (SensorSpec("Grid Soil", (Reading(600, hum=40.0),), 0),),
        "temp-only": (SensorSpec("Grid Soil", (Reading(600, temp=22.0),), 0),),
    }
    cases = [Case(id=f"series/{k}", family="series", at=BASE_AT, sensors=v) for k, v in shapes.items()]
    cases.extend(
        Case(id=f"series/{k}/inserted-desc", family="series", at=BASE_AT, sensors=shapes[k], insert_desc=True)
        for k in ("spike-low-mid", "spike-low-newest", "declining", "rising")
    )
    cases += _second_review_cases()
    for label, age in (
        ("0s", 0),
        ("1h", HOUR),
        ("24h-1s", DAY - 1),
        ("24h", DAY),
        ("24h+1s", DAY + 1),
        ("47h", 47 * HOUR),
        ("49h", 49 * HOUR),
    ):
        for temp in (None, 30.0):
            cases.append(
                Case(
                    id=f"series/freshness/{label}/current_temp={temp}",
                    family="series",
                    at=BASE_AT,
                    sensors=(_sensor(38.0, age_s=age, temp=22.0),),
                    current_temp=temp,
                )
            )
    # Irrigation cadence trends from the 7-day event history (all outside the 6h cooldown).
    high_freq = tuple(Event(age_s=7 * HOUR + i * 6 * HOUR) for i in range(26))
    low_freq = tuple(Event(age_s=(1 + i) * DAY, duration=1) for i in range(3))
    for tag, events in (("high-frequency", high_freq), ("low-frequency", low_freq)):
        for soil in (38.0, 75.0):
            cases.append(
                Case(
                    id=f"series/cadence/{tag}/m{soil:g}",
                    family="series",
                    at=BASE_AT,
                    sensors=(_sensor(soil),),
                    events=events,
                )
            )
    return cases


def _cooldown_family() -> list[Case]:
    cases: list[Case] = []
    six = 6 * HOUR
    ages = [
        ("1h", HOUR),
        ("6h-60s", six - 60),
        ("6h-1s", six - 1),
        ("6h", six),
        ("6h+1s", six + 1),
        ("6h+60s", six + 60),
        ("7h", 7 * HOUR),
    ]
    kinds = [("start", "auto"), ("start", "manual"), ("schedule_updated", "auto"), ("stop", "manual")]
    for soil in (38.0, 66.0):
        cases.append(Case(id=f"cooldown/none/m{soil:g}", family="cooldown", at=BASE_AT, sensors=(_sensor(soil),)))
        for action, trig in kinds:
            for label, age in ages:
                cases.append(
                    Case(
                        id=f"cooldown/{action}-{trig}/{label}/m{soil:g}",
                        family="cooldown",
                        at=BASE_AT,
                        sensors=(_sensor(soil),),
                        events=(Event(age_s=age, action=action, triggered_by=trig),),
                    )
                )
    combos = {
        "schedule_updated-1h+start-7h": (Event(HOUR, "schedule_updated"), Event(7 * HOUR)),
        "start-5h+start-2h": (Event(5 * HOUR, triggered_by="auto"), Event(2 * HOUR, triggered_by="manual")),
        "start-2h-no-duration": (Event(2 * HOUR, duration=None),),
    }
    for tag, events in combos.items():
        cases.append(
            Case(id=f"cooldown/combo/{tag}", family="cooldown", at=BASE_AT, sensors=(_sensor(38.0),), events=events)
        )
    cases.append(
        Case(
            id="cooldown/no-irrigator",
            family="cooldown",
            at=BASE_AT,
            sensors=(_sensor(38.0),),
            irrigator=False,
        )
    )
    cases.append(
        Case(
            id="cooldown/start-1h/no-sensors-fallback",
            family="cooldown",
            at=BASE_AT,
            events=(Event(HOUR),),
            current_temp=30.0,
        )
    )
    cases.append(
        Case(
            id="cooldown/start-1h/bypass-quiet",
            family="cooldown",
            at=BASE_AT,
            sensors=(_sensor(38.0),),
            events=(Event(HOUR, triggered_by="manual"),),
            bypass_quiet_hours=True,
            triggered_by="manual",
        )
    )
    return cases


def _quiet_family() -> list[Case]:
    cases: list[Case] = []
    configs: dict[str, ScheduleSetup] = {
        "global-0-5": {"global_quiet": (0, 5)},
        "global-22-6-wrap": {"global_quiet": (22, 6)},
        "global-0-5+cluster-0-0-disabled": {"global_quiet": (0, 5), "cluster_quiet": (0, 0)},
        "cluster-1-4": {"cluster_quiet": (1, 4)},
        "global-5-5-disabled": {"global_quiet": (5, 5)},
    }
    for cfg_key, cfg in configs.items():
        start, end = cfg.get("cluster_quiet") or cfg["global_quiet"]
        if start == end:
            start, end = 0, 5  # probe the would-be window edges anyway
        for tz in ("UTC", ROME):
            s_local = _local(tz, 2026, 4, 15, start, 0, 0)
            e_local = _local(tz, 2026, 4, 15 if end > start else 16, end, 0, 0)
            mid = s_local + (e_local - s_local) / 2
            instants = {
                "start-60s": s_local.timestamp() - 60,
                "start-1s": s_local.timestamp() - 1,
                "start": s_local.timestamp(),
                "start+1s": s_local.timestamp() + 1,
                "mid": mid.timestamp(),
                "end-1s": e_local.timestamp() - 1,
                "end": e_local.timestamp(),
                "end+1s": e_local.timestamp() + 1,
            }
            cases.extend(
                Case(
                    id=f"quiet/{cfg_key}/{tz}/{label}/bypass={bypass}",
                    family="quiet",
                    at=datetime.fromtimestamp(int(ts), tz=ZoneInfo("UTC")),
                    tz=tz,
                    sensors=(_sensor(38.0),),
                    bypass_quiet_hours=bypass,
                    triggered_by="manual" if bypass else "auto",
                    **cfg,
                )
                for label, ts in instants.items()
                for bypass in (False, True)
            )
    night = _utc(2026, 4, 15, 2, 0, 0)
    extras: dict[str, QuietExtraSetup] = {
        "cooldown-beats-quiet": {"events": (Event(HOUR),)},
        "quiet-beats-weather": {
            "environment": "outdoor",
            "weather": "forecast",
            "forecast": (("precipitation_mm", 9.0),),
        },
        "quiet-beats-water-warning": {"sensors": (_sensor(50.0, water_warning=True),)},
        "bypass+weather-skip": {
            "environment": "outdoor",
            "weather": "forecast",
            "forecast": (("precipitation_mm", 9.0),),
            "bypass_quiet_hours": True,
        },
        "bypass+fallback": {"sensors": (), "current_temp": 30.0, "bypass_quiet_hours": True},
        "bypass+water-warning": {"sensors": (_sensor(50.0, water_warning=True),), "bypass_quiet_hours": True},
        "no-plants-beats-quiet": {"plants": (), "sensors": (_sensor(38.0, plant=None),)},
    }
    for tag, kw in extras.items():
        base: QuietExtraSetup = {"sensors": (_sensor(38.0),)}
        base.update(kw)
        cases.append(Case(id=f"quiet/extra/{tag}", family="quiet", at=night, global_quiet=(0, 5), **base))
    return cases


def _windows_family() -> list[Case]:
    cases: list[Case] = []
    window_sets = {
        "none": (),
        "6-10": (Window(6, 10),),
        "22-6-wrap": (Window(22, 6),),
        "6-10-weekdays": (Window(6, 10, 0b0011111),),
        "6-8+17-19": (Window(6, 8), Window(17, 19)),
        "9-9-empty": (Window(9, 9),),
    }
    times = [(5, 59, 59), (6, 0, 0), (9, 59, 59), (10, 0, 0), (12, 0, 0), (18, 0, 0), (23, 0, 0), (3, 0, 0)]
    conditions: dict[str, tuple[SensorSpec, ...]] = {
        "dry": (_sensor(38.0),),
        "critical": (_sensor(20.0),),
        "wet": (_sensor(66.0),),
    }
    for ws_key, windows in window_sets.items():
        for h, m, s in times:
            for cond_key, sensors in conditions.items():
                cases.append(
                    Case(
                        id=f"windows/{ws_key}/{ROME}/{h:02d}:{m:02d}:{s:02d}/{cond_key}",
                        family="windows",
                        at=_local(ROME, 2026, 4, 15, h, m, s),
                        tz=ROME,
                        sensors=sensors,
                        windows=windows,
                    )
                )
        # Saturday probe for weekday masks + UTC-tz probe.
        cases.append(
            Case(
                id=f"windows/{ws_key}/{ROME}/saturday-07:00:00/dry",
                family="windows",
                at=_local(ROME, 2026, 4, 18, 7, 0, 0),
                tz=ROME,
                sensors=conditions["dry"],
                windows=windows,
            )
        )
        cases.append(
            Case(
                id=f"windows/{ws_key}/UTC/07:00:00-utc-is-09-rome/dry",
                family="windows",
                at=_utc(2026, 4, 15, 7, 0, 0),
                sensors=conditions["dry"],
                windows=windows,
            )
        )
    for tag, sensors in (
        ("water-warning", (_sensor(50.0, water_warning=True),)),
        ("over-watering", (_series([60.0, 64.0, 68.0, 72.0, 76.0, 80.0, 82.0, 84.0], step_s=2 * HOUR),)),
        ("no-sensors-fallback", ()),
    ):
        cases.append(
            Case(
                id=f"windows/6-10/{ROME}/12:00:00/{tag}",
                family="windows",
                at=_local(ROME, 2026, 4, 15, 12, 0, 0),
                tz=ROME,
                sensors=sensors,
                windows=window_sets["6-10"],
                current_temp=30.0 if not sensors else None,
            )
        )
    return cases


def _season_family() -> list[Case]:
    cases: list[Case] = []
    instants = {
        "winter-mid": (ROME, _local(ROME, 2026, 1, 15, 12, 0, 0)),
        "spring-mid": (ROME, _local(ROME, 2026, 4, 15, 12, 0, 0)),
        "summer-mid": (ROME, _local(ROME, 2026, 7, 15, 12, 0, 0)),
        "autumn-mid": (ROME, _local(ROME, 2026, 10, 15, 12, 0, 0)),
        "feb28-23:59:59-local": (ROME, _local(ROME, 2026, 2, 28, 23, 59, 59)),
        "mar01-00:00:00-local": (ROME, _local(ROME, 2026, 3, 1, 0, 0, 0)),
        "may31-23:59:59-local": (ROME, _local(ROME, 2026, 5, 31, 23, 59, 59)),
        "jun01-00:00:00-local": (ROME, _local(ROME, 2026, 6, 1, 0, 0, 0)),
        "aug31-23:59:59-local": (ROME, _local(ROME, 2026, 8, 31, 23, 59, 59)),
        "sep01-00:00:00-local": (ROME, _local(ROME, 2026, 9, 1, 0, 0, 0)),
        "nov30-23:59:59-local": (ROME, _local(ROME, 2026, 11, 30, 23, 59, 59)),
        "dec01-00:00:00-local": (ROME, _local(ROME, 2026, 12, 1, 0, 0, 0)),
        # Same instant as the Rome Mar-1 boundary, but preferences tz = UTC (still Feb 28).
        "mar01-00:00:00-rome-as-utc-prefs": ("UTC", _local(ROME, 2026, 3, 1, 0, 0, 0)),
        "mar01-00:00:00-utc": ("UTC", _utc(2026, 3, 1, 0, 0, 0)),
    }
    cases.extend(
        Case(
            id=f"season/{inst_key}/{plant_key}/{env}",
            family="season",
            at=at,
            tz=tz,
            environment=env,
            plants=plants,
            sensors=(_sensor(40.0, temp=22.0, hum=60.0, light=900),),
        )
        for inst_key, (tz, at) in instants.items()
        for plant_key, plants in {**PLANT_SETS, **SEASON_EXTRA_SETS}.items()
        for env in ("indoor", "outdoor")
    )
    return cases


def _dst_family() -> list[Case]:
    cases: list[Case] = []
    configs: dict[str, ScheduleSetup] = {
        "quiet-0-5": {"global_quiet": (0, 5)},
        "quiet-2-3": {"global_quiet": (2, 3)},
        "window-3-4": {"windows": (Window(3, 4),)},
    }
    days = {"spring-forward-2026-03-29": (2026, 3, 29), "fall-back-2026-10-25": (2026, 10, 25)}
    for day_key, (y, mo, d) in days.items():
        start = int(_utc(y, mo, d, 0, 0, 0).timestamp()) - 2 * HOUR
        for cfg_key, cfg in configs.items():
            for k in range(15):  # 22:00 UTC (eve) … 05:00 UTC, every 30 min
                ts = start + k * 30 * MINUTE
                at = datetime.fromtimestamp(ts, tz=ZoneInfo("UTC"))
                cases.append(
                    Case(
                        id=f"dst/{day_key}/{cfg_key}/{at.strftime('%Y%m%dT%H%MZ')}",
                        family="dst",
                        at=at,
                        tz=ROME,
                        sensors=(_sensor(38.0),),
                        **cfg,
                    )
                )
    return cases


def _vacation_family() -> list[Case]:
    cases: list[Case] = []
    vacs: dict[str, VacationSetup] = {
        "none": {},
        "active-no-capacity": {"vacation": (-DAY, 6 * DAY)},
        "active-plenty": {"vacation": (-DAY, 6 * DAY), "reservoir_l": 100.0, "flow_rate": 1.0},
        "active-partial-trim": {"vacation": (-DAY // 2, 9 * DAY + DAY // 2), "reservoir_l": 20.0, "flow_rate": 1.0},
        "active-tiny-exhausted": {"vacation": (-DAY // 2, 9 * DAY + DAY // 2), "reservoir_l": 10.0, "flow_rate": 1.0},
        "active-spent-trim": {
            "vacation": (-DAY // 2, 9 * DAY + DAY // 2),
            "reservoir_l": 100.0,
            "flow_rate": 1.0,
            "events": (Event(8 * HOUR, duration=8),),
        },
        "active-spent-exhausted": {
            "vacation": (-DAY // 2, 9 * DAY + DAY // 2),
            "reservoir_l": 100.0,
            "flow_rate": 1.0,
            "events": (Event(8 * HOUR, duration=9),),
        },
        "starts-now": {"vacation": (0, 3 * DAY)},
        "ends-now": {"vacation": (-3 * DAY, 0)},
        "ended-1s-ago": {"vacation": (-3 * DAY, -1)},
        "future": {"vacation": (DAY, 3 * DAY)},
        "flow-only": {"vacation": (-DAY, 6 * DAY), "flow_rate": 1.0},
        "active-no-irrigator": {"vacation": (-DAY, 6 * DAY), "irrigator": False},
    }
    cases.extend(
        Case(
            id=f"vacation/{vac_key}/monstera/m{soil:g}",
            family="vacation",
            at=BASE_AT,
            sensors=(_sensor(soil),),
            **kw,
        )
        for vac_key, kw in vacs.items()
        for soil in (38.0, 25.0, 66.0)
    )
    cases.extend(
        Case(
            id=f"vacation/{vac_key}/fern_cat/m50",
            family="vacation",
            at=BASE_AT,
            plants=PLANT_SETS["fern_cat"],
            sensors=(_sensor(50.0),),
            **vacs[vac_key],
        )
        for vac_key in ("active-partial-trim", "active-plenty")
    )
    cases.append(
        Case(
            id="vacation/active-tiny-exhausted/no-sensors-fallback",
            family="vacation",
            at=BASE_AT,
            current_temp=30.0,
            **vacs["active-tiny-exhausted"],
        )
    )
    return cases


def _leak_family() -> list[Case]:
    cases: list[Case] = []
    leaks = {
        "none": None,
        "open-1h": Leak("open", HOUR),
        "acknowledged-1h": Leak("acknowledged", HOUR),
        "resolved-1h": Leak("resolved", HOUR),
        "open-24h-60s": Leak("open", 24 * HOUR - 60),
        "open-24h": Leak("open", 24 * HOUR),
        "open-24h+1s": Leak("open", 24 * HOUR + 1),
        "open-other-cluster": Leak("open", HOUR, other_cluster=True),
    }
    for leak_key, leak in leaks.items():
        for bypass in (False, True):
            for cd_key, events in (("no-cooldown", ()), ("cooldown-1h", (Event(HOUR),))):
                cases.append(
                    Case(
                        id=f"leak/{leak_key}/bypass={bypass}/{cd_key}",
                        family="leak",
                        at=BASE_AT,
                        sensors=(_sensor(38.0),),
                        leak=leak,
                        events=events,
                        bypass_quiet_hours=bypass,
                        triggered_by="manual" if bypass else "auto",
                    )
                )
    night = _utc(2026, 4, 15, 2, 0, 0)
    cases.append(
        Case(
            id="leak/open-1h/during-quiet-hours",
            family="leak",
            at=night,
            global_quiet=(0, 5),
            sensors=(_sensor(38.0),),
            leak=leaks["open-1h"],
        )
    )
    cases.append(
        Case(
            id="leak/open-1h/no-plants",
            family="leak",
            at=BASE_AT,
            plants=(),
            sensors=(_sensor(38.0, plant=None),),
            leak=leaks["open-1h"],
        )
    )
    cases.append(
        Case(
            id="leak/open-1h/water-warning",
            family="leak",
            at=BASE_AT,
            sensors=(_sensor(50.0, water_warning=True),),
            leak=leaks["open-1h"],
        )
    )
    return cases


def _weather_family() -> list[Case]:
    cases: list[Case] = []
    clients: dict[str, WeatherSetup] = {
        "no-client": {"weather": "none"},
        "offline": {"weather": "offline"},
        "precip-0": {"weather": "forecast", "forecast": (("precipitation_mm", 0.0),)},
        "precip-2.0": {"weather": "forecast", "forecast": (("precipitation_mm", 2.0),)},
        "precip-2.01": {"weather": "forecast", "forecast": (("precipitation_mm", 2.01),)},
        "precip-5": {"weather": "forecast", "forecast": (("precipitation_mm", 5.0), ("max_temp", 20.0))},
        "precip-None": {"weather": "forecast", "forecast": (("precipitation_mm", None),)},
        "precip-missing": {"weather": "forecast", "forecast": (("max_temp", 20.0),)},
    }
    scenes: dict[str, SensorScene] = {
        "dry": {"sensors": (_sensor(38.0),)},
        "critical": {"sensors": (_sensor(20.0),)},
        "fallback-30C": {"sensors": (), "current_temp": 30.0},
    }
    for client_key, ckw in clients.items():
        for env in ("indoor", "outdoor", "greenhouse"):
            for scene_key, skw in scenes.items():
                cases.append(
                    Case(
                        id=f"weather/{client_key}/{env}/{scene_key}",
                        family="weather",
                        at=BASE_AT,
                        environment=env,
                        **ckw,
                        **skw,
                    )
                )
    cases.append(
        Case(
            id="weather/precip-5/outdoor/cooldown-first",
            family="weather",
            at=BASE_AT,
            environment="outdoor",
            sensors=(_sensor(38.0),),
            events=(Event(HOUR),),
            **clients["precip-5"],
        )
    )
    cases.append(
        Case(
            id="weather/precip-5/outdoor/vacation-active",
            family="weather",
            at=BASE_AT,
            environment="outdoor",
            sensors=(_sensor(38.0),),
            vacation=(-DAY, DAY),
            **clients["precip-5"],
        )
    )
    return cases


def _fallback_family() -> list[Case]:
    cases: list[Case] = []
    temps = [None, -5.0, 18.0, 18.1, 24.0, 24.1, 28.0, 28.1, 35.0]
    configs = {
        "no-config": (),
        "smart-4min-10h": (("mode", "smart"), ("duration_minutes", 4), ("interval_hours", 10)),
        "manual": (("mode", "manual"),),
    }
    cases.extend(
        Case(
            id=f"fallback/{plant_key}/{cfg_key}/temp={temp}",
            family="fallback",
            at=BASE_AT,
            plants=PLANT_SETS[plant_key],
            cluster_config=cfg,
            current_temp=temp,
        )
        for plant_key in ("monstera", "fern_cat", "cactus_cat")
        for cfg_key, cfg in configs.items()
        for temp in temps
    )
    for temp in (None, 30.0):
        cases.append(
            Case(
                id=f"fallback/sensor-without-readings/temp={temp}",
                family="fallback",
                at=BASE_AT,
                sensors=(SensorSpec("Grid Soil", (), 0),),
                current_temp=temp,
            )
        )
        cases.append(
            Case(
                id=f"fallback/only-stale-readings-25h/temp={temp}",
                family="fallback",
                at=BASE_AT,
                sensors=(_sensor(20.0, age_s=25 * HOUR, temp=22.0),),
                current_temp=temp,
            )
        )
    cases.append(
        Case(
            id="fallback/config-row-from-quiet-override-only/temp=None",
            family="fallback",
            at=BASE_AT,
            cluster_quiet=(0, 0),
        )
    )
    return cases


def _learning_family() -> list[Case]:
    cases: list[Case] = []
    cases.extend(
        Case(
            id=f"learning/{seed or 'none'}/m{soil:g}",
            family="learning",
            at=BASE_AT,
            sensors=(_sensor(soil, temp=22.0),),
            learning=seed,
        )
        for seed in (None, "blocked_drip", "rapid_drainage")
        for soil in (38.0, 55.0)
    )
    dry_dim_air = tuple(Reading(age_s=i * HOUR, soil=20.0, hum=20.0, light=18) for i in range(6))
    cases.append(
        Case(
            id="learning/conflict/two-sensors-low-light-dry-air",
            family="learning",
            at=BASE_AT,
            plants=MIXED_PLANTS,
            sensors=(
                SensorSpec("Soil A", dry_dim_air, 0),
                SensorSpec("Soil B", tuple(Reading(age_s=i * HOUR, soil=80.0) for i in range(3)), 2),
            ),
            learning="conflict",
        )
    )
    cases.append(
        Case(
            id="learning/blocked_drip/two-sensors",
            family="learning",
            at=BASE_AT,
            plants=MIXED_PLANTS,
            sensors=(_sensor(38.0, name="Soil A"), _sensor(70.0, name="Soil B", plant=2)),
            learning="blocked_drip",
        )
    )
    return cases


def _misc_family() -> list[Case]:
    return [
        Case(id="misc/missing-cluster", family="misc", at=BASE_AT, sensors=(_sensor(38.0),), cluster_exists=False),
        Case(id="misc/no-plants", family="misc", at=BASE_AT, plants=(), sensors=(_sensor(38.0, plant=None),)),
        Case(id="misc/no-plants-no-sensors", family="misc", at=BASE_AT, plants=()),
        Case(id="misc/no-irrigator-dry", family="misc", at=BASE_AT, irrigator=False, sensors=(_sensor(38.0),)),
        # Sensor moves (issue #145): a reading measured in the old pot never judges the new plant.
        Case(
            id="misc/sensor-just-moved",
            family="misc",
            at=BASE_AT,
            plants=MIXED_PLANTS,
            sensors=(
                SensorSpec(
                    name="Moved Soil",
                    readings=(Reading(age_s=HOUR, soil=20.0, temp=22.0), Reading(age_s=300, soil=60.0, temp=22.0)),
                    plant=1,
                    moved=(0, 600),
                ),
            ),
        ),
        Case(
            id="misc/sensor-moved-settled",
            family="misc",
            at=BASE_AT,
            plants=MIXED_PLANTS,
            sensors=(
                SensorSpec(
                    name="Moved Soil",
                    readings=(Reading(age_s=3 * HOUR, soil=20.0, temp=22.0), Reading(age_s=HOUR, soil=60.0, temp=22.0)),
                    plant=1,
                    moved=(0, 2 * HOUR),
                ),
            ),
        ),
        Case(
            id="misc/manual-trigger-dry",
            family="misc",
            at=BASE_AT,
            sensors=(_sensor(38.0),),
            triggered_by="manual",
        ),
        Case(
            id="misc/everything-at-once",
            family="misc",
            at=_local(ROME, 2026, 7, 15, 8, 0, 0),
            tz=ROME,
            environment="outdoor",
            plants=MIXED_PLANTS,
            sensors=(
                _series([50.0, 48.0, 46.0, 44.0, 42.0, 40.0, 38.0, 36.0], step_s=2 * HOUR, temp=31.0, hum=30.0),
                _sensor(62.0, name="Soil B", plant=1, temp=31.0, hum=30.0, light=1600),
            ),
            windows=(Window(6, 10),),
            vacation=(-DAY, 5 * DAY),
            reservoir_l=30.0,
            flow_rate=1.0,
            weather="forecast",
            forecast=(("precipitation_mm", 1.5),),
        ),
    ]


FAMILIES: dict[str, Callable[[], list[Case]]] = {
    "soil": _soil_family,
    "driest": _driest_family,
    "series": _series_family,
    "cooldown": _cooldown_family,
    "quiet": _quiet_family,
    "windows": _windows_family,
    "season": _season_family,
    "dst": _dst_family,
    "vacation": _vacation_family,
    "leak": _leak_family,
    "weather": _weather_family,
    "fallback": _fallback_family,
    "learning": _learning_family,
    "misc": _misc_family,
}


# Families whose golden is split into one file per value of this id segment
# (keeps every golden well under the repo's 512 KB large-file hook).
_SHARD_SEGMENT = {"soil": 1, "season": 2, "quiet": 1, "windows": 1, "dst": 1}


def golden_name(case: Case) -> str:
    """Golden file (relative to ``tests/golden/``) that stores ``case``'s record."""
    segment = _SHARD_SEGMENT.get(case.family)
    if segment is None:
        return f"engine/{case.family}.jsonl"
    return f"engine/{case.family}/{case.id.split('/')[segment]}.jsonl"


def build_cases(family: str | None = None) -> list[Case]:
    """Return the grid (or one family of it); ids are unique and stable."""
    names = [family] if family is not None else list(FAMILIES)
    cases = [case for name in names for case in FAMILIES[name]()]
    duplicates = sorted(i for i, n in Counter(c.id for c in cases).items() if n > 1)
    if duplicates:
        msg = f"duplicate case ids: {duplicates}"
        raise ValueError(msg)
    return cases
