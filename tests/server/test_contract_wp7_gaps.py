"""Characterization gaps — pin scheduler / irrigation-pipeline branches before restructuring.

Each test pins what ``greenhouse_server.scheduler`` and ``greenhouse_server.services.irrigation``
do TODAY on a branch or ordering that the original characterization suite left uncovered (by
coverage) or that extracting the job plumbing could silently change: job-session
scaffolding (commit / rollback / close order, which errors escape), the run-time vs schedule-time
reads of the pump-watcher job, the temperature-source matrix, the actuation clock seam, partial
counts when ``rearm_leak_checks`` fails mid-scan, and every early-return shape. Nothing here
asserts what the code *should* do.

Hermetic: in-memory SQLite, frozen clock (``frozen_clock``), offline weather, fake device
adapters and a fake APScheduler; the process-wide scheduler is never started.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NoReturn, Self, TypeVar, Unpack, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from engine_grid import plant_database
from fake_devices import FakeIrrigatorAdapter
from golden import FROZEN_TS
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import HealthAlarm
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.logic.decision import Action, IrrigationDecision, TriggerCode
from greenhouse_core.models import (
    ActivityEvent,
    ActivitySource,
    Base,
    EntityType,
    EventAction,
    IrrigationEvent,
    TriggeredBy,
)
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server import scheduler as sched
from greenhouse_server.services import irrigation as irrigation_mod
from greenhouse_server.services import irrigation_jobs as irrigation_jobs_mod
from greenhouse_server.services import pump_watcher as pump_watcher_module
from greenhouse_server.services import pump_watcher_jobs as pump_watcher_jobs_mod
from greenhouse_server.services.irrigation import IrrigationService
from greenhouse_server.services.pump_watcher import PumpWatcherService
from greenhouse_server.services.pump_watcher_jobs import handle_watcher_interrupted, schedule_pump_watcher
from server.conftest import run_core_job
from server.test_contract_pipeline import Pipeline, RecordingGateway

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

    import time_machine
    from fastapi import FastAPI
    from sqlalchemy import Engine
    from time_machine import Traveller

    from greenhouse_core.models import Irrigator
    from greenhouse_core.plant_db import PlantDatabase
    from greenhouse_server.services.health_monitor import DeviceHealthMonitor
    from greenhouse_server.services.irrigation import MonitorResult, PipelineHooks
    from greenhouse_server.services.pump_watcher import PumpWatcherOptions, WatchOutcome
    from greenhouse_server.services.sync import SyncService
    from greenhouse_server.services.weather import WeatherClient

_Row = TypeVar("_Row", IrrigationEvent, ActivityEvent)

IRRIGATION_LOGGER = "greenhouse_server.services.irrigation"
SCHEDULER_LOGGER = "greenhouse_server.scheduler"


# ── Shared fakes ──────────────────────────────────────────────────────────────


class FakeScheduler:
    """A running scheduler stand-in that records ``add_job`` calls."""

    def __init__(self) -> None:
        """Report running, with no jobs yet."""
        self.running = True
        self.jobs: list[dict[str, Any]] = []  # add_job's keyword arguments, as APScheduler takes them

    def add_job(self, func: Callable[[], object], trigger: object, **kwargs: object) -> None:
        """Record the job."""
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})


class StubSync:
    """``SyncService`` stand-in: records ``ensure_fresh_and_read`` calls, returns a canned snapshot."""

    def __init__(self, data: Mapping[str, float | None] | None) -> None:
        """Serve ``data`` as the cluster snapshot (``None`` = no data) and record each read."""
        self.data = data
        self.calls: list[int] = []

    def ensure_fresh_and_read(self, cluster_id: int) -> dict[str, float | None] | None:
        """Record the cluster and return a copy of the canned snapshot."""
        self.calls.append(cluster_id)
        return None if self.data is None else dict(self.data)


class StubWeather:
    """Weather stand-in with a fixed current reading and no forecast."""

    def __init__(self, current: Mapping[str, float | None] | None) -> None:
        """Serve ``current`` (``None`` = weather unavailable) and count reads."""
        self.current = current
        self.calls = 0

    def get_current(self) -> dict[str, float | None] | None:
        """Count the read and return a copy of the canned reading."""
        self.calls += 1
        return None if self.current is None else dict(self.current)

    def get_forecast(self, hours: int = 6) -> None:  # noqa: ARG002 — mirrors WeatherClient
        """No forecast is available."""
        return


class StubPlantDb:
    """Plant-DB stand-in serving the same care data for every species."""

    def __init__(self, care: dict[str, object]) -> None:
        """Serve ``care`` for every lookup."""
        self.care = care

    def get_care_data(self, species: str | None = None) -> dict[str, object]:  # noqa: ARG002 — mirrors PlantDatabase
        """Return a copy of the canned care data."""
        return dict(self.care)


def _service(
    db: IrrigationRepository,
    registry: DeviceRegistry | None,
    sync: StubSync,
    weather: StubWeather | None,
    plants: PlantDatabase | StubPlantDb,
    **hooks: Unpack[PipelineHooks],
) -> IrrigationService:
    """An IrrigationService on the duck-typed stand-ins above, cast to the collaborators they replace."""
    return IrrigationService(
        db,
        registry,
        cast("SyncService", sync),
        cast("WeatherClient", weather),
        cast("PlantDatabase", plants),
        **hooks,
    )


def _decision(cluster_id: int, *, log_id: int | None = None) -> IrrigationDecision:
    decision = IrrigationDecision(
        cluster_id=cluster_id,
        evaluated_at=FROZEN_TS,
        action=Action.IRRIGATE,
        duration_minutes=4,
        interval_hours=6,
        confidence=0.75,
        decision_log_id=log_id,
    )
    decision.add_reason(code=TriggerCode.SENSOR_DRY, message="soil low")
    return decision


@pytest.fixture
def db(tmp_db: IrrigationRepository, frozen_clock: time_machine.Traveller, clean_env: Path) -> IrrigationRepository:
    """The in-memory repository, with a frozen clock and a clean environment."""
    del frozen_clock, clean_env  # requested only for their side effects
    return tmp_db


@dataclass
class GapPump:
    """What the ``pump`` fixture wires up."""

    cluster_id: int
    irrigator_id: int
    adapter: FakeIrrigatorAdapter
    registry: DeviceRegistry


@pytest.fixture
def pump(db: IrrigationRepository) -> GapPump:
    """One cluster (+ one irrigator) and a registry resolving it to a recording fake adapter."""
    cid = db.add_cluster("Gap Pump Cluster")
    iid = db.add_irrigator(
        cluster_id=cid,
        tuya_device_id="fake_tuya_device_gap00001",
        name="Gap Pump",
        irrigator_type="rainpoint.ik10pw",
        config={},
    )
    db.session.commit()
    adapter = FakeIrrigatorAdapter()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda: adapter)
    return GapPump(cluster_id=cid, irrigator_id=iid, adapter=adapter, registry=registry)


def _patch_decide(
    monkeypatch: pytest.MonkeyPatch, factory: Callable[[int], IrrigationDecision | None]
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake(_self: IrrigationLogic, cluster_id: int, **kwargs: object) -> IrrigationDecision | None:
        calls.append({"cluster_id": cluster_id, **kwargs})
        return factory(cluster_id)

    monkeypatch.setattr(IrrigationLogic, "decide_for_cluster", fake)
    return calls


def _rows(db: IrrigationRepository, model: type[_Row]) -> list[_Row]:
    return list(db.session.query(model).order_by(model.id))


# ── Pipeline: early returns ───────────────────────────────────────────────────


def test_pipeline_without_a_decision_returns_the_no_data_error(
    db: IrrigationRepository, pump: GapPump, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pipeline without a decision returns the no data error."""
    _patch_decide(monkeypatch, lambda _cid: None)
    svc = _service(db, pump.registry, StubSync(None), StubWeather(None), plant_database())
    result = svc.run_irrigation_pipeline(pump.cluster_id, no_sync=True)
    assert result == {"action": "error", "reason": "no data for decision", "confidence": 0}
    assert [type(v) for v in result.values()] == [str, str, int]
    assert _rows(db, ActivityEvent) == []
    assert pump.adapter.calls == []


def test_pipeline_unknown_cluster_returns_the_not_found_error_without_deciding(
    db: IrrigationRepository, pump: GapPump, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pipeline unknown cluster returns the not found error without deciding."""
    calls = _patch_decide(monkeypatch, _decision)
    sync = StubSync({"temperature": 22.0})
    svc = _service(db, pump.registry, sync, StubWeather(None), plant_database())
    assert svc.run_irrigation_pipeline(999) == {"action": "error", "reason": "cluster not found", "confidence": 0}
    assert calls == []
    assert sync.calls == []


@pytest.mark.parametrize("force", [False, True])
def test_pipeline_builds_the_engine_and_passes_force_flags(
    db: IrrigationRepository, pump: GapPump, monkeypatch: pytest.MonkeyPatch, *, force: bool
) -> None:
    """Pipeline builds the engine and passes force flags."""
    built: list[tuple[object, ...]] = []
    decided: list[dict[str, object]] = []

    class SpyLogic:
        def __init__(
            self, repo: IrrigationRepository, plant_db: PlantDatabase, *, weather_client: WeatherClient | None
        ) -> None:
            built.append((repo, plant_db, weather_client))

        def decide_for_cluster(self, cluster_id: int, **kwargs: object) -> None:
            decided.append({"cluster_id": cluster_id, **kwargs})

    monkeypatch.setattr(irrigation_mod, "IrrigationLogic", SpyLogic)
    weather, plants = StubWeather({"feels_like": 18.0}), plant_database()
    svc = _service(db, pump.registry, StubSync(None), weather, plants)
    svc.run_irrigation_pipeline(pump.cluster_id, temp_override=21.5, force=force)
    assert built == [(db, plants, weather)]
    assert decided == [
        {
            "cluster_id": pump.cluster_id,
            "current_temp": 21.5,
            "persist": True,
            "triggered_by": "manual" if force else "auto",
            "bypass_quiet_hours": force,
        }
    ]


# ── Pipeline: temperature-source matrix (seen through the actuation notes) ──────


TEMP_MATRIX = [
    # environment, override, no_sync, sensor snapshot, weather now, expected temp, source, sync calls, weather calls
    ("indoor", 19.0, False, {"temperature": 22.0, "soil_moisture": 31.0}, {"feels_like": 16.0}, 19.0, "override", 0, 0),
    ("indoor", None, False, {"temperature": 22.0, "soil_moisture": 31.0}, {"feels_like": 16.0}, 22.0, "sensor", 1, 0),
    ("indoor", None, False, {"soil_moisture": 31.0}, {"feels_like": 16.0}, 16.0, "open-meteo (fallback)", 1, 1),
    ("indoor", None, False, {"soil_moisture": 31.0}, {"feels_like": None}, 20.0, "fallback (20C)", 1, 1),
    ("indoor", None, True, None, None, 20.0, "fallback (20C)", 0, 1),
    (
        "outdoor",
        None,
        False,
        {"temperature": 22.0, "soil_moisture": 31.0},
        {"feels_like": 16.0},
        16.0,
        "open-meteo",
        1,
        1,
    ),
    (
        "outdoor",
        None,
        False,
        {"temperature": 22.0, "soil_moisture": 31.0},
        None,
        22.0,
        "sensor (weather unavailable)",
        1,
        1,
    ),
    ("outdoor", None, False, {"soil_moisture": 31.0}, {"feels_like": None}, 20.0, "fallback (20C)", 1, 1),
    ("outdoor", None, False, {}, None, 20.0, "fallback (20C)", 1, 1),
    ("outdoor", None, True, None, {"feels_like": 16.0}, 16.0, "open-meteo", 0, 1),
    ("outdoor", None, True, None, None, 20.0, "fallback (20C)", 0, 1),
]


@pytest.mark.parametrize(
    ("environment", "override", "no_sync", "snapshot", "now", "temp", "source", "sync_calls", "weather_calls"),
    TEMP_MATRIX,
)
def test_temperature_source_matrix_and_soil_note(  # noqa: PLR0913, PLR0917 — one parameter per parametrize argname
    db: IrrigationRepository,
    pump: GapPump,
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    override: float | None,
    snapshot: dict[str, float] | None,
    now: dict[str, float | None] | None,
    temp: float,
    source: str,
    sync_calls: int,
    weather_calls: int,
    *,
    no_sync: bool,
) -> None:
    """Temperature source matrix and soil note."""
    db.update_cluster(pump.cluster_id, environment=environment)
    db.session.commit()
    _patch_decide(monkeypatch, _decision)
    sync, weather = StubSync(snapshot), StubWeather(now)
    svc = _service(db, pump.registry, sync, weather, plant_database())
    result = svc.run_irrigation_pipeline(pump.cluster_id, temp_override=override, no_sync=no_sync)
    assert (result["temperature"], result["temperature_source"]) == (temp, source)
    assert (len(sync.calls), weather.calls) == (sync_calls, weather_calls)
    soil = ", soil=31% (driest)" if override is None and snapshot and "soil_moisture" in snapshot else ""
    (event,) = _rows(db, IrrigationEvent)
    assert event.notes == f"temp={temp:.1f}C ({source}){soil}, confidence=75%, reason=soil low"


# ── Pipeline: actuation path ──────────────────────────────────────────────────


class NotBlockingMonitor:
    """Health-monitor stand-in that never blocks actuation and records who asked."""

    def __init__(self) -> None:
        """Start with no questions asked."""
        self.asked: list[int] = []

    def is_actuation_blocked(self, irrigator: Irrigator) -> tuple[bool, list[HealthAlarm]]:
        """Record the irrigator and answer: not blocked."""
        self.asked.append(irrigator.id)
        return False, []


def test_health_monitor_that_does_not_block_lets_the_pump_start(
    db: IrrigationRepository, pump: GapPump, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Health monitor that does not block lets the pump start."""
    _patch_decide(monkeypatch, _decision)
    monitor = NotBlockingMonitor()
    svc = _service(
        db,
        pump.registry,
        StubSync(None),
        StubWeather(None),
        plant_database(),
        health_monitor=cast("DeviceHealthMonitor", monitor),
    )
    result = svc.run_irrigation_pipeline(pump.cluster_id, no_sync=True)
    assert monitor.asked == [pump.irrigator_id]
    assert pump.adapter.calls == [("start", pump.irrigator_id, 4)]
    assert result["action"] == "irrigated"
    assert "blocking_alarms" not in result
    assert [(a.code, a.severity) for a in _rows(db, ActivityEvent)] == [("irrigated", "info")]


@pytest.mark.parametrize("log_id", [None, 4242])
def test_successful_start_marks_the_decision_actuated_only_with_a_log_id(
    db: IrrigationRepository, pump: GapPump, monkeypatch: pytest.MonkeyPatch, log_id: int | None
) -> None:
    """Successful start marks the decision actuated only with a log ID."""
    _patch_decide(monkeypatch, lambda cid: _decision(cid, log_id=log_id))
    marked: list[int] = []
    monkeypatch.setattr(IrrigationRepository, "set_decision_actuated", lambda _self, i, *_a: marked.append(i))
    svc = _service(db, pump.registry, StubSync(None), StubWeather(None), plant_database())
    assert svc.run_irrigation_pipeline(pump.cluster_id, no_sync=True)["action"] == "irrigated"
    assert marked == ([] if log_id is None else [log_id])


def test_started_at_is_read_after_the_adapter_start(
    db: IrrigationRepository, pump: GapPump, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Started at is read after the adapter start."""
    clock = {"now": 1_000_000.0}

    class Clock:
        @staticmethod
        def time() -> float:
            return clock["now"]

    real_start = pump.adapter.start

    def slow_start(irrigator: Irrigator, minutes: int | None = None) -> tuple[bool, str]:
        clock["now"] += 100.0
        return real_start(irrigator, minutes)

    pump.adapter.start = slow_start  # type: ignore[method-assign]
    monkeypatch.setattr(irrigation_mod, "_time", Clock)
    _patch_decide(monkeypatch, _decision)
    svc = _service(db, pump.registry, StubSync(None), StubWeather(None), plant_database())
    svc.run_irrigation_pipeline(pump.cluster_id, no_sync=True)
    (event,) = _rows(db, IrrigationEvent)
    assert (event.action, event.timestamp) == ("start", 1_000_100)


# ── monitor_cluster / check_cluster early returns and target parsing ──────────


def test_monitor_unknown_cluster_shape(db: IrrigationRepository) -> None:
    """Monitor unknown cluster shape."""
    sync = StubSync(None)
    svc = _service(db, None, sync, None, plant_database())
    expected: MonitorResult = {"cluster_name": "unknown", "sensors": [], "needs_water": []}
    assert svc.monitor_cluster(999) == expected
    assert sync.calls == []


def test_check_unknown_cluster_shape(db: IrrigationRepository) -> None:
    """Check unknown cluster shape."""
    svc = _service(db, None, StubSync(None), None, plant_database())
    assert svc.check_cluster(999) == {
        "cluster_id": 999,
        "cluster_name": "unknown",
        "action": "error",
        "notes": "not found",
    }


@pytest.mark.parametrize(
    ("care", "band", "status"),
    [
        # D10: one parser everywhere — parse_moisture_target reads the first two parts.
        ({"soil_moisture_target": "40-50-60"}, (40.0, 50.0), "dry"),
        ({"soil_moisture_target": "50"}, (45.0, 65.0), "dry"),
        ({"soil_moisture_target": "high-low"}, (45.0, 65.0), "dry"),
        ({"soil_moisture_target": None}, (45.0, 65.0), "dry"),
        ({}, (45.0, 65.0), "dry"),
        ({"soil_moisture_target": "20-30"}, (20.0, 30.0), "ok"),
        ({"soil_moisture_target": " 20 - 30 "}, (20.0, 30.0), "ok"),
    ],
)
def test_monitor_target_band_parse_and_fallback(
    db: IrrigationRepository, care: dict[str, object], band: tuple[float, float], status: str
) -> None:
    """Monitor target band parse and fallback."""
    cid = db.add_cluster("Band Gap")
    sid = db.add_sensor(
        cluster_id=cid,
        tuya_device_id="fake_tuya_sensor_band0001",
        name="Band Probe",
        sensor_type="tuya.tr301z",
        config={},
    )
    db.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=33.0)
    svc = _service(db, None, StubSync(None), None, StubPlantDb(care))
    out = svc.monitor_cluster(cid)
    (row,) = out["sensors"]
    assert (row["target_min"], row["target_max"], row["status"]) == (*band, status)
    assert out["needs_water"] == (["Band Probe (unknown): 33%"] if status == "dry" else [])


def test_monitor_latest_soil_skips_missing_values_and_reports_no_data(db: IrrigationRepository) -> None:
    """Monitor latest soil skips missing values and reports no data."""
    cid = db.add_cluster("Latest Gap")
    s1 = db.add_sensor(
        cluster_id=cid, tuya_device_id="fake_tuya_sensor_lat0001", name="P1", sensor_type="soil", config={}
    )
    s2 = db.add_sensor(
        cluster_id=cid, tuya_device_id="fake_tuya_sensor_lat0002", name="P2", sensor_type="soil", config={}
    )
    db.add_sensor_reading(sensor_id=s1, timestamp=FROZEN_TS - 1200, soil_moisture=52.0)
    db.add_sensor_reading(sensor_id=s1, timestamp=FROZEN_TS - 600, soil_moisture=None, temperature=21.0)
    db.add_sensor_reading(sensor_id=s2, timestamp=FROZEN_TS - 600, soil_moisture=None, temperature=21.0)
    svc = _service(db, None, StubSync(None), None, StubPlantDb({}))
    rows = svc.monitor_cluster(cid)["sensors"]
    assert [(r["sensor_name"], r["soil_moisture"], r["status"]) for r in rows] == [
        ("P1", 52.0, "ok"),
        ("P2", None, "no_data"),
    ]


# ── check_all_clusters: the failure is logged from inside the except ──────────


def test_check_all_failure_log_carries_the_exception(
    db: IrrigationRepository, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Check all failure log carries the exception."""
    cid = db.add_cluster("Crashy")
    db.session.commit()

    class BoomError(RuntimeError):
        pass

    def explode(_self: IrrigationService, _cluster_id: int) -> NoReturn:
        msg = "kaput"
        raise BoomError(msg)

    monkeypatch.setattr(IrrigationService, "check_cluster", explode)
    svc = _service(db, None, StubSync(None), None, plant_database())
    with caplog.at_level(logging.ERROR, logger=IRRIGATION_LOGGER):
        results = svc.check_all_clusters()
    assert results == [
        {"cluster_id": cid, "cluster_name": "Crashy", "action": "error", "notes": "check failed: BoomError('kaput')"}
    ]
    (record,) = caplog.records
    assert record.exc_info is not None
    assert (record.levelname, record.getMessage(), record.exc_info[0]) == (
        "ERROR",
        f"Check failed for cluster {cid}",
        BoomError,
    )


# ── handle_watcher_interrupted: activity write failure is swallowed ────────────


@pytest.mark.parametrize(("triggered_by", "stopped"), [("auto", True), ("manual", False)])
def test_watcher_shutdown_activity_failure_is_logged_not_raised(
    db: IrrigationRepository, pump: GapPump, caplog: pytest.LogCaptureFixture, triggered_by: str, *, stopped: bool
) -> None:
    """Watcher shutdown activity failure is logged not raised."""

    def broken(_self: IrrigationRepository, **_kwargs: object) -> NoReturn:
        msg = "activity table locked"
        raise RuntimeError(msg)

    irrigator = db.get_irrigator(pump.irrigator_id)
    assert irrigator is not None
    with pytest.MonkeyPatch.context() as mp, caplog.at_level(logging.ERROR, logger=IRRIGATION_LOGGER):
        mp.setattr(IrrigationRepository, "add_activity_event", broken)
        assert (
            handle_watcher_interrupted(
                db, pump.registry, irrigator, triggered_by=TriggeredBy(triggered_by), started_at=FROZEN_TS - 30
            )
            is stopped
        )
    (record,) = [r for r in caplog.records if r.levelname == "ERROR"]
    assert record.getMessage() == f"Failed to record watcher-shutdown activity for irrigator {pump.irrigator_id}"
    assert record.exc_info is not None


# ── schedule_pump_watcher: schedule-time captures vs run-time reads ───────────


def _watch_gap_db() -> tuple[Engine, sessionmaker[Session], int]:
    """A shared in-memory DB with one cluster and one irrigator: ``(engine, factory, irrigator_id)``."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    repo = IrrigationRepository(session)
    cid = repo.add_cluster("Watch Gap")
    iid = repo.add_irrigator(
        cluster_id=cid, tuya_device_id="wg", name="Watch Gap Pump", irrigator_type="rainpoint.ik10pw", config={}
    )
    session.commit()
    session.close()
    return engine, factory, iid


def _watch_gap_app(factory: sessionmaker[Session]) -> tuple[SimpleNamespace, SimpleNamespace, DeviceRegistry]:
    """A stand-in ``_app`` with watcher settings and a fake-adapter registry: ``(app, settings, registry)``."""
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", FakeIrrigatorAdapter)
    settings = SimpleNamespace(
        pump_watcher_enabled=True,
        pump_watcher_poll_seconds=0.7,
        pump_watcher_warmup_seconds=3.0,
        pump_watcher_max_read_failures=4,
    )
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, device_registry=registry, session_factory=factory))
    return app, settings, registry


@dataclass
class WatcherEnv:
    """What the ``watcher_env`` fixture wires up."""

    app: SimpleNamespace
    settings: SimpleNamespace
    registry: DeviceRegistry
    scheduler: FakeScheduler
    built: list[dict[str, object]]
    irrigator_id: int
    wait: Callable[[float], bool]
    stop: Callable[[], bool]
    factory: sessionmaker[Session]


@pytest.fixture
def watcher_env(monkeypatch: pytest.MonkeyPatch, frozen_clock: Traveller) -> Iterator[WatcherEnv]:
    """A stand-in app and fake scheduler for scheduling pump watchers, with a spying watcher class."""
    del frozen_clock  # requested only for its side effect
    engine, factory, iid = _watch_gap_db()
    app, settings, registry = _watch_gap_app(factory)
    fake = FakeScheduler()

    def original_wait(_s: float) -> bool:
        return False

    def original_stop() -> bool:
        return False

    monkeypatch.setattr(sched, "scheduler", fake)
    monkeypatch.setattr(sched, "_app", app)
    monkeypatch.setattr(sched, "shutdown_requested", original_stop)
    monkeypatch.setattr(sched, "wait_for_shutdown", original_wait)
    built: list[dict[str, object]] = []

    class SpyWatcher(PumpWatcherService):
        def __init__(
            self, repo: IrrigationRepository, registry: DeviceRegistry, **kwargs: Unpack[PumpWatcherOptions]
        ) -> None:
            built.append({"repo": repo, "registry": registry, **kwargs})
            super().__init__(repo, registry, **kwargs)

        def watch(self, irrigator: Irrigator, duration_seconds: int, *, started_at: int | None = None) -> WatchOutcome:
            built[-1]["watched"] = (irrigator.id, duration_seconds, started_at)
            return cast("WatchOutcome", {"outcome": "completed"})  # the job reads only the outcome

    monkeypatch.setattr(pump_watcher_module, "PumpWatcherService", SpyWatcher)
    yield WatcherEnv(
        app=app,
        settings=settings,
        registry=registry,
        scheduler=fake,
        built=built,
        irrigator_id=iid,
        wait=original_wait,
        stop=original_stop,
        factory=factory,
    )
    engine.dispose()


def test_watcher_job_without_a_health_monitor_passes_none(watcher_env: WatcherEnv) -> None:
    """Watcher job without a health monitor passes none."""
    assert not hasattr(watcher_env.app.state, "health_monitor")
    assert schedule_pump_watcher(watcher_env.irrigator_id, 2, FROZEN_TS) is True
    watcher_env.scheduler.jobs[0]["func"]()
    (built,) = watcher_env.built
    assert built["monitor"] is None
    assert built["watched"] == (watcher_env.irrigator_id, 120, FROZEN_TS)


def test_watcher_tuning_is_read_when_the_job_runs(watcher_env: WatcherEnv) -> None:
    """Watcher tuning is read when the job runs."""
    schedule_pump_watcher(watcher_env.irrigator_id, 2, FROZEN_TS)
    watcher_env.settings.pump_watcher_poll_seconds = 9.5
    watcher_env.settings.pump_watcher_warmup_seconds = 1.25
    watcher_env.settings.pump_watcher_max_read_failures = 11
    watcher_env.settings.pump_watcher_enabled = False  # only consulted at schedule time
    watcher_env.scheduler.jobs[0]["func"]()
    (built,) = watcher_env.built
    assert (built["poll_seconds"], built["warmup_seconds"], built["max_read_failures"]) == (9.5, 1.25, 11)


def test_watcher_settings_removed_after_scheduling_fall_back_to_defaults(watcher_env: WatcherEnv) -> None:
    """Watcher settings removed after scheduling fall back to defaults."""
    schedule_pump_watcher(watcher_env.irrigator_id, 2, FROZEN_TS)
    watcher_env.app.state.settings = None
    watcher_env.scheduler.jobs[0]["func"]()
    (built,) = watcher_env.built
    assert (built["poll_seconds"], built["warmup_seconds"], built["max_read_failures"]) == (2.0, 5.0, 5)
    assert [type(v) for v in (built["poll_seconds"], built["warmup_seconds"], built["max_read_failures"])] == [
        float,
        float,
        int,
    ]


def test_watcher_monitor_is_read_when_the_job_runs(watcher_env: WatcherEnv) -> None:
    """Watcher monitor is read when the job runs."""
    schedule_pump_watcher(watcher_env.irrigator_id, 2, FROZEN_TS)
    bound: list[IrrigationRepository] = []
    view = SimpleNamespace()

    def for_repo(repo: IrrigationRepository) -> SimpleNamespace:
        bound.append(repo)
        return view

    watcher_env.app.state.health_monitor = SimpleNamespace(for_repo=for_repo)
    watcher_env.scheduler.jobs[0]["func"]()
    (built,) = watcher_env.built
    assert built["monitor"] is view  # the watcher's own view of the shared monitor (S6)
    assert bound == [built["repo"]]


def test_watcher_job_keeps_the_app_and_hooks_captured_at_schedule_time(
    watcher_env: WatcherEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Watcher job keeps the app and hooks captured at schedule time."""
    schedule_pump_watcher(watcher_env.irrigator_id, 2, FROZEN_TS)
    other_registry = DeviceRegistry()
    opened: list[int] = []
    other_app = SimpleNamespace(
        state=SimpleNamespace(settings=None, device_registry=other_registry, session_factory=lambda: opened.append(1))
    )
    monkeypatch.setattr(sched, "_app", other_app)
    monkeypatch.setattr(sched, "wait_for_shutdown", lambda _s: True)
    monkeypatch.setattr(sched, "shutdown_requested", lambda: True)
    watcher_env.app.state.device_registry = other_registry  # the registry object was captured too
    watcher_env.scheduler.jobs[0]["func"]()
    (built,) = watcher_env.built
    assert opened == []
    assert built["registry"] is watcher_env.registry
    assert built["sleep"] is watcher_env.wait
    assert built["stop_requested"] is watcher_env.stop
    assert (built["poll_seconds"], built["max_read_failures"]) == (0.7, 4)


# ── rearm_leak_checks: a failure mid-scan keeps the partial count ─────────────


def _two_recent_auto_starts() -> tuple[Engine, sessionmaker[Session], list[int]]:
    """Two clusters, each with an automatic start 60 s ago: ``(engine, factory, cluster_ids)``."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    repo = IrrigationRepository(session)
    ids: list[int] = []
    for n in range(2):
        cid = repo.add_cluster(f"Rearm {n}")
        iid = repo.add_irrigator(
            cluster_id=cid, tuya_device_id=f"rg{n}", name=f"P{n}", irrigator_type="rainpoint.ik10pw", config={}
        )
        repo.add_irrigation_event(
            irrigator_id=iid,
            action=EventAction.START,
            triggered_by=TriggeredBy.AUTO,
            duration_minutes=3,
            timestamp=FROZEN_TS - 60,
        )
        ids.append(cid)
    session.commit()
    session.close()
    return engine, factory, ids


@pytest.mark.usefixtures("frozen_clock")
def test_rearm_failure_mid_scan_keeps_partial_count_and_logs_both_lines(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Rearm failure mid scan keeps partial count and logs both lines."""
    engine, factory, ids = _two_recent_auto_starts()
    fake = FakeScheduler()
    monkeypatch.setattr(sched, "scheduler", fake)
    monkeypatch.setattr(sched, "_app", SimpleNamespace(state=SimpleNamespace(session_factory=factory)))
    real = IrrigationRepository.get_recent_events
    seen: list[int] = []
    failing_call = 2  # the second irrigator's lookup fails

    def flaky(self: IrrigationRepository, irrigator_id: int, hours: int = 24) -> list[IrrigationEvent]:
        seen.append(irrigator_id)
        if len(seen) == failing_call:
            msg = "db gone"
            raise RuntimeError(msg)
        return real(self, irrigator_id, hours=hours)

    monkeypatch.setattr(IrrigationRepository, "get_recent_events", flaky)
    with caplog.at_level(logging.INFO, logger=IRRIGATION_LOGGER):
        assert irrigation_jobs_mod.rearm_leak_checks() == 1
    assert [j["id"] for j in fake.jobs] == [f"leak-check-{ids[0]}-{FROZEN_TS - 60}"]
    assert [(r.levelname, r.getMessage(), r.exc_info is not None) for r in caplog.records] == [
        ("ERROR", "Re-arming leak checks after restart failed", True),
        ("INFO", "Re-armed 1 post-irrigation leak check(s) after restart", False),
    ]
    engine.dispose()


# ── Scheduler job bodies: session scaffolding ─────────────────────────────────


@pytest.fixture
def p(clean_env: Path, frozen_clock: Traveller) -> Iterator[Pipeline]:
    """A fresh pipeline harness with a clean environment and a frozen clock."""
    del clean_env, frozen_clock  # requested only for their side effects
    pipeline = Pipeline()
    yield pipeline
    pipeline.close()


class SpySession:
    """Wraps a real session and logs commit / rollback / close in call order."""

    def __init__(self, real: Session, log: list[str]) -> None:
        """Wrap ``real``, logging transaction calls into ``log``."""
        self._real = real
        self._log = log

    def commit(self) -> None:
        """Log and commit the real session."""
        self._log.append("commit")
        self._real.commit()

    def rollback(self) -> None:
        """Log and roll back the real session."""
        self._log.append("rollback")
        self._real.rollback()

    def close(self) -> None:
        """Log and close the real session."""
        self._log.append("close")
        self._real.close()

    def __getattr__(self, name: str) -> object:
        """Delegate everything else to the real session."""
        return getattr(self._real, name)


def _spy_sessions(monkeypatch: pytest.MonkeyPatch, app: FastAPI) -> list[str]:
    log: list[str] = []
    real_factory = app.state.session_factory

    def factory() -> SpySession:
        log.append("open")
        return SpySession(real_factory(), log)

    monkeypatch.setattr(app.state, "session_factory", factory)
    return log


class StubMonitor:
    """Health-monitor stand-in that logs binds, polls and gate questions in call order."""

    def __init__(self, log: list[str]) -> None:
        """Log into ``log`` and remember every repository a view binds to."""
        self.log = log
        self.repos: list[IrrigationRepository] = []

    def for_repo(self, repo: IrrigationRepository) -> Self:
        """Log the bind, remember the repository and act as the view itself."""
        self.log.append("bind")
        self.repos.append(repo)
        return self

    def poll_all(self) -> None:
        """Log the poll."""
        self.log.append("poll")

    def is_actuation_blocked(self, irrigator: Irrigator) -> tuple[bool, list[HealthAlarm]]:
        """Log the question and answer: not blocked."""
        self.log.append(f"blocked?{irrigator.id}")
        return False, []


JOBS: dict[str, tuple[str, str | None, str]] = {
    "sync": ("_sync_job", "greenhouse_server.services.sync.SyncService.sync_all_sensors", "Sync job failed"),
    "snapshot": (
        "_health_snapshot_job",
        "greenhouse_server.services.health.PlantHealthService.snapshot_daily",
        "Plant health snapshot job failed",
    ),
    "check": (
        "_check_job",
        "greenhouse_server.services.irrigation.IrrigationService.check_all_clusters",
        "Check job failed",
    ),
    "anomaly": (
        "_anomaly_job",
        "greenhouse_server.services.anomaly.SensorAnomalyService.scan",
        "Anomaly scan job failed",
    ),
    "monitor": ("_health_monitor_job", None, "Device health monitor job failed"),
}


def _wire_job(p: Pipeline, log: list[str]) -> StubMonitor:
    p.app.state.device_gateway = RecordingGateway()
    monitor = StubMonitor(log)
    p.app.state.health_monitor = monitor
    return monitor


@pytest.mark.parametrize("job", list(JOBS))
def test_job_success_commits_then_closes(p: Pipeline, monkeypatch: pytest.MonkeyPatch, job: str) -> None:
    """Job success commits then closes."""
    func_name, _, _ = JOBS[job]
    log = _spy_sessions(monkeypatch, p.app)
    _wire_job(p, log)
    getattr(sched, func_name)()
    assert [e for e in log if e in ("open", "commit", "rollback", "close")] == ["open", "commit", "close"]


@pytest.mark.parametrize("job", list(JOBS))
def test_job_failure_rolls_back_logs_with_traceback_and_closes(
    p: Pipeline, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, job: str
) -> None:
    """Job failure rolls back logs with traceback and closes."""
    func_name, target, message = JOBS[job]
    log = _spy_sessions(monkeypatch, p.app)
    monitor = _wire_job(p, log)

    class JobBoomError(RuntimeError):
        pass

    def explode(*_a: object, **_kw: object) -> NoReturn:
        raise JobBoomError(job)

    if target is None:
        monkeypatch.setattr(monitor, "poll_all", explode)
    else:
        monkeypatch.setattr(target, explode)
    with caplog.at_level(logging.ERROR, logger=SCHEDULER_LOGGER):
        getattr(sched, func_name)()  # must not raise
    assert [e for e in log if e in ("open", "commit", "rollback", "close")] == ["open", "rollback", "close"]
    records = [r for r in caplog.records if r.name == SCHEDULER_LOGGER]
    assert [(r.levelname, r.getMessage()) for r in records] == [("ERROR", message)]
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is JobBoomError


@pytest.mark.parametrize("job", ["snapshot", "check", "anomaly", "monitor"])
def test_session_factory_failure_escapes_the_job(p: Pipeline, monkeypatch: pytest.MonkeyPatch, job: str) -> None:
    """Session factory failure escapes the job."""
    func_name, _, _ = JOBS[job]
    _wire_job(p, [])

    def broken_factory() -> NoReturn:
        msg = "db file locked"
        raise ConnectionError(msg)

    monkeypatch.setattr(p.app.state, "session_factory", broken_factory)
    with pytest.raises(ConnectionError, match="db file locked"):
        getattr(sched, func_name)()


@pytest.mark.usefixtures("p")
@pytest.mark.parametrize(
    ("job", "escapes"),
    [("sync", False), ("snapshot", True), ("check", True), ("anomaly", True), ("monitor", False)],
)
def test_jobs_without_an_app(monkeypatch: pytest.MonkeyPatch, job: str, *, escapes: bool) -> None:
    """Jobs without an app."""
    func_name, _, _ = JOBS[job]
    monkeypatch.setattr(sched, "_app", None)
    if escapes:
        with pytest.raises(AttributeError):
            getattr(sched, func_name)()
    else:
        assert getattr(sched, func_name)() is None


def _spy_service_init(monkeypatch: pytest.MonkeyPatch, log: list[str]) -> list[dict[str, Any]]:
    """Record every ``IrrigationService`` construction (logging "service"); return the kwargs list."""
    built: list[dict[str, Any]] = []  # the constructor's keyword arguments, as passed
    real_init: Callable[..., None] = IrrigationService.__init__  # forwarded verbatim

    def spy_init(self: IrrigationService, *args: object, **kwargs: object) -> None:
        log.append("service")
        built.append(kwargs)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(IrrigationService, "__init__", spy_init)
    return built


def test_check_job_binds_the_monitor_to_its_repo_before_the_pipeline_asks_it(
    p: Pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check job binds the monitor to its repo before the pipeline asks it."""
    ids = p.seed_cluster(soil=35.0)
    p.app.state.device_registry = p.wiring.registry
    log: list[str] = []
    monitor = StubMonitor(log)
    p.app.state.health_monitor = monitor
    built = _spy_service_init(monkeypatch, log)
    run_core_job(sched.CHECK_ALL_JOB_ID)
    assert log == ["bind", "service", f"blocked?{ids['irrigator_id']}"]
    (kwargs,) = built
    assert list(kwargs) == [
        "repo",
        "registry",
        "sync_service",
        "weather_client",
        "plant_db",
        "health_monitor",
        "notifier",
    ]
    assert kwargs["repo"] is monitor.repos[0]
    assert kwargs["health_monitor"] is monitor
    assert kwargs["registry"] is p.wiring.registry
    assert kwargs["weather_client"] is p.app.state.weather_client
    assert kwargs["plant_db"] is p.app.state.plant_db
    assert kwargs["notifier"] is p.notifier
    assert kwargs["sync_service"]._repo is kwargs["repo"]  # noqa: SLF001 — SyncService exposes no repository accessor
    assert [c[0] for c in p.wiring.irrigator.calls] == ["start"]


# ── Mutation survivors (mutants no other test killed) ─────────────────────────


def test_job_skip_guards_log_at_debug(p: Pipeline, caplog: pytest.LogCaptureFixture) -> None:
    """Mutation survivors: the two pre-session guards each log one debug line."""
    assert getattr(p.app.state, "device_gateway", None) is None
    assert getattr(p.app.state, "health_monitor", None) is None
    with caplog.at_level(logging.DEBUG, logger=SCHEDULER_LOGGER):
        run_core_job("sensor_sync")
        run_core_job("device_health_monitor")
    assert [(r.levelname, r.getMessage()) for r in caplog.records if r.name == SCHEDULER_LOGGER] == [
        ("DEBUG", "Sync job skipped: no Tuya credentials"),
        ("DEBUG", "Health monitor job skipped: no monitor wired"),
    ]


@pytest.mark.parametrize("outcome", ["completed", "interrupted", "boom"])
def test_watcher_job_session_commit_rollback_close_order(
    watcher_env: WatcherEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, outcome: str
) -> None:
    """Mutation survivor: the watcher job rolls back on failure, commits only after an interrupt, always closes."""
    log: list[str] = []
    real_factory = watcher_env.factory

    def factory() -> SpySession:
        log.append("open")
        return SpySession(real_factory(), log)

    watcher_env.app.state.session_factory = factory

    def watch(
        _self: PumpWatcherService, _irrigator: Irrigator, _duration_seconds: int, *, started_at: int | None = None
    ) -> dict[str, str]:
        del started_at  # the job passes it by keyword
        if outcome == "boom":
            msg = "watch exploded"
            raise RuntimeError(msg)
        return {"outcome": outcome}

    monkeypatch.setattr(pump_watcher_module.PumpWatcherService, "watch", watch)  # the spy subclass
    monkeypatch.setattr(
        pump_watcher_jobs_mod, "handle_watcher_interrupted", lambda *_a, **_kw: log.append("interrupted")
    )
    schedule_pump_watcher(watcher_env.irrigator_id, 2, FROZEN_TS, triggered_by=TriggeredBy.MANUAL)
    with caplog.at_level(logging.ERROR, logger=IRRIGATION_LOGGER):
        watcher_env.scheduler.jobs[0]["func"]()  # must not raise
    assert (
        log
        == {
            "completed": ["open", "close"],
            "interrupted": ["open", "interrupted", "commit", "close"],
            "boom": ["open", "rollback", "close"],
        }[outcome]
    )
    failed = [f"Pump watcher job failed for irrigator {watcher_env.irrigator_id}"]
    assert caplog.messages == (failed if outcome == "boom" else [])


def test_leak_check_done_sees_a_marker_behind_many_newer_leak_rows(db: IrrigationRepository) -> None:
    """B-23 fixed: the completion scan is bounded by time, not by the newest 500 leak rows.

    A completed check followed by 600 newer leak-source rows (other starts' checks) still
    counts as done; markers written before the start never do.
    """
    cid = db.add_cluster("Scan Gap")
    db.add_activity_event(
        source=ActivitySource.LEAK, entity_type=EntityType.CLUSTER, entity_id=cid, code="leak_check", message="m",
        payload={"started_at": FROZEN_TS}, timestamp=FROZEN_TS - 1,
    )  # fmt: skip
    assert irrigation_jobs_mod._leak_check_done(db, cid, FROZEN_TS) is False  # noqa: SLF001 — leak-check jobs have no public entry point
    db.add_activity_event(
        source=ActivitySource.LEAK, entity_type=EntityType.CLUSTER, entity_id=cid, code="leak_check", message="m",
        payload={"started_at": FROZEN_TS}, timestamp=FROZEN_TS + 1800,
    )  # fmt: skip
    for i in range(600):
        db.add_activity_event(
            source=ActivitySource.LEAK, entity_type=EntityType.CLUSTER, entity_id=cid, code="leak_check", message="m",
            payload={"started_at": FROZEN_TS + 3600 + i}, timestamp=FROZEN_TS + 5400 + i,
        )  # fmt: skip
    assert irrigation_jobs_mod._leak_check_done(db, cid, FROZEN_TS) is True  # noqa: SLF001 — leak-check jobs have no public entry point


def test_leak_check_done_ignores_other_codes_and_empty_payloads(db: IrrigationRepository) -> None:
    """Mutation survivor: only ``leak_check`` / ``leak_hold`` rows with a payload count as a completed check."""
    cid = db.add_cluster("Done Gap")
    for code, payload in (("leak_suspect", {"started_at": FROZEN_TS}), ("leak_check", None)):
        db.add_activity_event(
            source=ActivitySource.LEAK,
            entity_type=EntityType.CLUSTER,
            entity_id=cid,
            code=code,
            message="m",
            payload=payload,
        )
    assert irrigation_jobs_mod._leak_check_done(db, cid, FROZEN_TS) is False  # noqa: SLF001 — leak-check jobs have no public entry point
    db.add_activity_event(
        source=ActivitySource.LEAK,
        entity_type=EntityType.CLUSTER,
        entity_id=cid,
        code="leak_hold",
        message="m",
        payload={"started_at": FROZEN_TS},
    )
    assert irrigation_jobs_mod._leak_check_done(db, cid, FROZEN_TS) is True  # noqa: SLF001 — leak-check jobs have no public entry point


@pytest.mark.usefixtures("frozen_clock")
@pytest.mark.parametrize("fail", [False, True])
def test_rearm_always_closes_its_session(monkeypatch: pytest.MonkeyPatch, *, fail: bool) -> None:
    """Mutation survivor: ``rearm_leak_checks`` closes its session on success and after a failure."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    real_factory = sessionmaker(bind=engine)
    log: list[str] = []

    def factory() -> SpySession:
        log.append("open")
        return SpySession(real_factory(), log)

    monkeypatch.setattr(sched, "scheduler", FakeScheduler())
    monkeypatch.setattr(sched, "_app", SimpleNamespace(state=SimpleNamespace(session_factory=factory)))
    if fail:
        monkeypatch.setattr(IrrigationRepository, "list_all_irrigators", lambda _self: 1 / 0)
    assert irrigation_jobs_mod.rearm_leak_checks() == 0
    assert log == ["open", "close"]
    engine.dispose()


def test_health_block_names_the_first_blocking_alarm(
    db: IrrigationRepository, pump: GapPump, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation survivor: the device-health gate reports ``blocking_alarms[0]`` as the primary alarm."""

    class Blocking:
        def is_actuation_blocked(self, _irrigator: Irrigator) -> tuple[bool, list[HealthAlarm]]:
            return True, [HealthAlarm.NO_WATER, HealthAlarm.DEVICE_OFFLINE]

    _patch_decide(monkeypatch, _decision)
    svc = _service(
        db,
        pump.registry,
        StubSync(None),
        StubWeather(None),
        plant_database(),
        health_monitor=cast("DeviceHealthMonitor", Blocking()),
    )
    result = svc.run_irrigation_pipeline(pump.cluster_id, no_sync=True)
    assert pump.adapter.calls == []
    assert (result["action"], result["reason"], result["blocking_alarms"]) == (
        "skip",
        "Actuation blocked by device health: no_water on 'Gap Pump'; soil low",
        ["no_water", "device_offline"],
    )
    # B-6: the block leads the trail, so it is the decision's primary code.
    assert [r["code"] for r in result["reasons"]] == ["device_no_water", "sensor_dry"]
    (activity,) = _rows(db, ActivityEvent)
    assert (activity.code, activity.severity) == ("decision_skip", "warning")


def test_monitor_always_syncs(db: IrrigationRepository) -> None:
    """Mutation survivor: ``monitor_cluster`` refreshes the cluster through the sync service (D15: always)."""
    cid = db.add_cluster("Sync Gap")
    sync = StubSync(None)
    svc = _service(db, None, sync, None, StubPlantDb({}))
    expected: MonitorResult = {"cluster_name": "Sync Gap", "sensors": [], "needs_water": []}
    assert svc.monitor_cluster(cid) == expected
    assert sync.calls == [cid]


def test_monitor_looks_back_two_hours(db: IrrigationRepository) -> None:
    """Mutation survivor: monitor reads the last 2 h of readings.

    A 1.5 h-old sample counts, a 2.5 h-old one does not.
    """
    cid = db.add_cluster("Lookback Gap")
    fresh = db.add_sensor(
        cluster_id=cid, tuya_device_id="fake_tuya_sensor_lb01", name="F", sensor_type="soil", config={}
    )
    stale = db.add_sensor(
        cluster_id=cid, tuya_device_id="fake_tuya_sensor_lb02", name="S", sensor_type="soil", config={}
    )
    db.add_sensor_reading(sensor_id=fresh, timestamp=FROZEN_TS - 5400, soil_moisture=50.0)
    db.add_sensor_reading(sensor_id=stale, timestamp=FROZEN_TS - 9000, soil_moisture=50.0)
    svc = _service(db, None, StubSync(None), None, StubPlantDb({}))
    rows = svc.monitor_cluster(cid)["sensors"]
    assert [(r["sensor_name"], r["soil_moisture"], r["status"]) for r in rows] == [
        ("F", 50.0, "ok"),
        ("S", None, "no_data"),
    ]


@pytest.mark.parametrize(
    ("soil", "status"), [(5.0, "dry"), (4.9, "very_dry"), (19.9, "dry"), (20.0, "ok"), (40.0, "ok"), (40.1, "wet")]
)
def test_monitor_status_ladder_edges(db: IrrigationRepository, soil: float, status: str) -> None:
    """Mutation survivor: the 5-way ladder is strict ``<`` / ``>`` at every edge (band 20-30)."""
    cid = db.add_cluster("Ladder Gap")
    sid = db.add_sensor(cluster_id=cid, tuya_device_id="fake_tuya_sensor_lad1", name="L", sensor_type="soil", config={})
    db.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=soil)
    svc = _service(db, None, StubSync(None), None, StubPlantDb({"soil_moisture_target": "20-30"}))
    (row,) = svc.monitor_cluster(cid)["sensors"]
    assert row["status"] == status


def _record_check_steps(monkeypatch: pytest.MonkeyPatch, log: list[str]) -> None:
    """Replace every step ``check_cluster`` takes with a stub that logs its name (canned results)."""

    def learning(*_a: object) -> list[dict[str, int]]:
        log.append("learning")
        return [{"a": 1}]

    def maintenance(*_a: object) -> list[dict[str, int]]:
        log.append("maintenance")
        return [{"m": 1}]

    def pipeline(_self: IrrigationService, _c: int) -> dict[str, object]:
        log.append("pipeline")
        return {"action": "skip", "reason": "why", "confidence": 0.5}

    def monitor(_self: IrrigationService, _c: int) -> dict[str, list[str]]:
        log.append("monitor")
        return {"needs_water": ["x"]}

    monkeypatch.setattr(irrigation_mod, "collect_learning_alerts", learning)
    monkeypatch.setattr(irrigation_mod, "collect_maintenance_alerts", maintenance)
    monkeypatch.setattr(
        irrigation_mod, "sync_cluster_alerts", lambda _repo, c, _pdb, *, notifier: log.append(f"sync:{c}:{notifier}")
    )
    monkeypatch.setattr(IrrigationService, "run_irrigation_pipeline", pipeline)
    monkeypatch.setattr(IrrigationService, "monitor_cluster", monitor)


@pytest.mark.parametrize("branch", ["monitored", "auto_run_off", "pipeline"])
def test_check_cluster_call_order_and_result_shape(
    db: IrrigationRepository, monkeypatch: pytest.MonkeyPatch, branch: str
) -> None:
    """Mutation survivor: learning → maintenance → (config) → pipeline/monitor → ``sync_cluster_alerts`` → result."""
    cid = db.add_cluster("Order Gap")
    if branch != "monitored":
        db.add_irrigator(
            cluster_id=cid,
            tuya_device_id="fake_tuya_device_ord1",
            name="O",
            irrigator_type="rainpoint.ik10pw",
            config={},
        )
    if branch == "auto_run_off":
        db.set_irrigation_config(cid, auto_run=False)
    db.session.commit()
    log: list[str] = []

    _record_check_steps(monkeypatch, log)
    svc = _service(db, None, StubSync(None), None, StubPlantDb({}))
    result = svc.check_cluster(cid)
    middle = {"monitored": ["monitor"], "auto_run_off": [], "pipeline": ["pipeline"]}[branch]
    assert log == ["learning", "maintenance", *middle, f"sync:{cid}:None"]
    detail = {
        "monitored": ("monitored", "needs_water", ["x"]),
        "auto_run_off": ("skipped", "notes", "auto_run disabled"),
        "pipeline": ("skip", "notes", "why"),
    }[branch]
    assert list(result.items()) == [
        ("cluster_id", cid),
        ("cluster_name", "Order Gap"),
        ("action", detail[0]),
        (detail[1], detail[2]),
        ("alerts", [{"a": 1}]),
        ("maintenance", [{"m": 1}]),
    ]
