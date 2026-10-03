"""WP7 characterization gaps — pin scheduler / irrigation-pipeline branches before restructuring.

Each test pins what ``greenhouse_server.scheduler`` and ``greenhouse_server.services.irrigation``
do TODAY on a branch or ordering that the Phase-1 net left uncovered (coverage precondition,
plan §0.4 G.1) or that a WP7 extraction could silently change (target §3.1 / §3.8): job-session
scaffolding (commit / rollback / close order, which errors escape), the run-time vs schedule-time
reads of the pump-watcher job, the temperature-source matrix, the actuation clock seam, partial
counts when ``rearm_leak_checks`` fails mid-scan, and every early-return shape. Nothing here
asserts what the code *should* do.

Hermetic: in-memory SQLite, frozen clock (``frozen_clock``), offline weather, fake device
adapters and a fake APScheduler; the process-wide scheduler is never started.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from engine_grid import plant_database
from fake_devices import FakeIrrigatorAdapter
from golden import FROZEN_TS
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.logic.decision import Action, IrrigationDecision, TriggerCode
from greenhouse_core.models import ActivityEvent, Base, IrrigationEvent
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server import scheduler as sched
from greenhouse_server.services import irrigation as irrigation_mod
from greenhouse_server.services import pump_watcher as pump_watcher_module
from greenhouse_server.services.irrigation import IrrigationService, handle_watcher_interrupted, schedule_pump_watcher
from greenhouse_server.services.pump_watcher import PumpWatcherService
from server.test_contract_pipeline import Pipeline

IRRIGATION_LOGGER = "greenhouse_server.services.irrigation"
SCHEDULER_LOGGER = "greenhouse_server.scheduler"


# ── Shared fakes ──────────────────────────────────────────────────────────────


class FakeScheduler:
    def __init__(self) -> None:
        self.running = True
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})


class StubSync:
    """``SyncService`` stand-in: records ``ensure_fresh_and_read`` calls, returns a canned snapshot."""

    def __init__(self, data: dict | None) -> None:
        self.data = data
        self.calls: list[int] = []

    def ensure_fresh_and_read(self, cluster_id: int):
        self.calls.append(cluster_id)
        return None if self.data is None else dict(self.data)


class StubWeather:
    def __init__(self, current: dict | None) -> None:
        self.current = current
        self.calls = 0

    def get_current(self):
        self.calls += 1
        return None if self.current is None else dict(self.current)

    def get_forecast(self, hours: int = 6):  # noqa: ARG002 — mirrors WeatherClient
        return None


class StubPlantDb:
    def __init__(self, care: dict) -> None:
        self.care = care

    def get_care_data(self, species=None):  # noqa: ARG002 — mirrors PlantDatabase
        return dict(self.care)


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
def db(tmp_db, frozen_clock, clean_env):
    return tmp_db


@pytest.fixture
def pump(db):
    """One cluster (+ one irrigator) and a registry resolving it to a recording fake adapter."""
    cid = db.add_cluster("Gap Pump Cluster")
    iid = db.add_irrigator(
        cluster_id=cid,
        tuya_device_id="fake_tuya_device_gap00001",
        name="Gap Pump",
        irrigator_type="tuya_cloud",
        config={},
    )
    db.session.commit()
    adapter = FakeIrrigatorAdapter()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda: adapter)
    return SimpleNamespace(cluster_id=cid, irrigator_id=iid, adapter=adapter, registry=registry)


def _patch_decide(monkeypatch, factory) -> list[dict]:
    calls: list[dict] = []

    def fake(self, cluster_id, **kwargs):
        calls.append({"cluster_id": cluster_id, **kwargs})
        return factory(cluster_id)

    monkeypatch.setattr(IrrigationLogic, "decide_for_cluster", fake)
    return calls


def _rows(db, model) -> list:
    return list(db.session.query(model).order_by(model.id))


# ── Pipeline: early returns ───────────────────────────────────────────────────


def test_pipeline_without_a_decision_returns_the_no_data_error(db, pump, monkeypatch):
    _patch_decide(monkeypatch, lambda cid: None)
    svc = IrrigationService(db, pump.registry, StubSync(None), StubWeather(None), plant_database())
    result = svc.run_irrigation_pipeline(pump.cluster_id, no_sync=True)
    assert result == {"action": "error", "reason": "no data for decision", "confidence": 0}
    assert [type(v) for v in result.values()] == [str, str, int]
    assert _rows(db, ActivityEvent) == [] and pump.adapter.calls == []


def test_pipeline_unknown_cluster_returns_the_not_found_error_without_deciding(db, pump, monkeypatch):
    calls = _patch_decide(monkeypatch, _decision)
    sync = StubSync({"temperature": 22.0})
    svc = IrrigationService(db, pump.registry, sync, StubWeather(None), plant_database())
    assert svc.run_irrigation_pipeline(999) == {"action": "error", "reason": "cluster not found", "confidence": 0}
    assert calls == [] and sync.calls == []


@pytest.mark.parametrize("force", [False, True])
def test_pipeline_builds_the_engine_and_passes_force_flags(db, pump, monkeypatch, force):
    built: list[tuple] = []
    decided: list[dict] = []

    class SpyLogic:
        def __init__(self, repo, plant_db, *, weather_client):
            built.append((repo, plant_db, weather_client))

        def decide_for_cluster(self, cluster_id, **kwargs):
            decided.append({"cluster_id": cluster_id, **kwargs})
            return None

    monkeypatch.setattr(irrigation_mod, "IrrigationLogic", SpyLogic)
    weather, plants = StubWeather({"feels_like": 18.0}), plant_database()
    svc = IrrigationService(db, pump.registry, StubSync(None), weather, plants)
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
def test_temperature_source_matrix_and_soil_note(
    db, pump, monkeypatch, environment, override, no_sync, snapshot, now, temp, source, sync_calls, weather_calls
):
    db.update_cluster(pump.cluster_id, environment=environment)
    db.session.commit()
    _patch_decide(monkeypatch, _decision)
    sync, weather = StubSync(snapshot), StubWeather(now)
    svc = IrrigationService(db, pump.registry, sync, weather, plant_database())
    result = svc.run_irrigation_pipeline(pump.cluster_id, temp_override=override, no_sync=no_sync)
    assert (result["temperature"], result["temperature_source"]) == (temp, source)
    assert (len(sync.calls), weather.calls) == (sync_calls, weather_calls)
    soil = ", soil=31% (driest)" if override is None and snapshot and "soil_moisture" in snapshot else ""
    (event,) = _rows(db, IrrigationEvent)
    assert event.notes == f"temp={temp:.1f}C ({source}){soil}, confidence=75%, reason=soil low"


# ── Pipeline: actuation path ──────────────────────────────────────────────────


class NotBlockingMonitor:
    def __init__(self) -> None:
        self.asked: list[int] = []

    def is_actuation_blocked(self, irrigator):
        self.asked.append(irrigator.id)
        return False, []


def test_health_monitor_that_does_not_block_lets_the_pump_start(db, pump, monkeypatch):
    _patch_decide(monkeypatch, _decision)
    monitor = NotBlockingMonitor()
    svc = IrrigationService(
        db, pump.registry, StubSync(None), StubWeather(None), plant_database(), health_monitor=monitor
    )
    result = svc.run_irrigation_pipeline(pump.cluster_id, no_sync=True)
    assert monitor.asked == [pump.irrigator_id]
    assert pump.adapter.calls == [("start", pump.irrigator_id, 4)]
    assert result["action"] == "irrigated" and "blocking_alarms" not in result
    assert [(a.code, a.severity) for a in _rows(db, ActivityEvent)] == [("irrigated", "info")]


@pytest.mark.parametrize("log_id", [None, 4242])
def test_successful_start_marks_the_decision_actuated_only_with_a_log_id(db, pump, monkeypatch, log_id):
    _patch_decide(monkeypatch, lambda cid: _decision(cid, log_id=log_id))
    marked: list = []
    monkeypatch.setattr(IrrigationRepository, "set_decision_actuated", lambda self, i, *a: marked.append(i))
    svc = IrrigationService(db, pump.registry, StubSync(None), StubWeather(None), plant_database())
    assert svc.run_irrigation_pipeline(pump.cluster_id, no_sync=True)["action"] == "irrigated"
    assert marked == ([] if log_id is None else [log_id])


def test_started_at_is_read_after_the_adapter_start(db, pump, monkeypatch):
    clock = {"now": 1_000_000.0}

    class Clock:
        @staticmethod
        def time():
            return clock["now"]

    real_start = pump.adapter.start

    def slow_start(irrigator, minutes=None):
        clock["now"] += 100.0
        return real_start(irrigator, minutes)

    pump.adapter.start = slow_start  # type: ignore[method-assign]
    monkeypatch.setattr(irrigation_mod, "_time", Clock)
    _patch_decide(monkeypatch, _decision)
    svc = IrrigationService(db, pump.registry, StubSync(None), StubWeather(None), plant_database())
    svc.run_irrigation_pipeline(pump.cluster_id, no_sync=True)
    (event,) = _rows(db, IrrigationEvent)
    assert (event.action, event.timestamp) == ("start", 1_000_100)


# ── monitor_cluster / check_cluster early returns and target parsing ──────────


def test_monitor_unknown_cluster_shape(db):
    sync = StubSync(None)
    svc = IrrigationService(db, None, sync, None, plant_database())
    assert svc.monitor_cluster(999) == {"cluster_name": "unknown", "sensors": [], "needs_water": []}
    assert sync.calls == []


def test_check_unknown_cluster_shape(db):
    svc = IrrigationService(db, None, StubSync(None), None, plant_database())
    assert svc.check_cluster(999) == {
        "cluster_id": 999,
        "cluster_name": "unknown",
        "action": "error",
        "notes": "not found",
    }


@pytest.mark.parametrize(
    ("care", "band", "status"),
    [
        ({"soil_moisture_target": "40-50-60"}, (45.0, 65.0), "dry"),
        ({"soil_moisture_target": "50"}, (45.0, 65.0), "dry"),
        ({"soil_moisture_target": "high-low"}, (45.0, 65.0), "dry"),
        ({"soil_moisture_target": None}, (45.0, 65.0), "dry"),
        ({}, (45.0, 65.0), "dry"),
        ({"soil_moisture_target": "20-30"}, (20.0, 30.0), "ok"),
        ({"soil_moisture_target": " 20 - 30 "}, (20.0, 30.0), "ok"),
    ],
)
def test_monitor_target_band_parse_and_fallback(db, care, band, status):
    cid = db.add_cluster("Band Gap")
    sid = db.add_sensor(
        cluster_id=cid,
        tuya_device_id="fake_tuya_sensor_band0001",
        name="Band Probe",
        sensor_type="soil_moisture",
        config={},
    )
    db.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=33.0)
    svc = IrrigationService(db, None, StubSync(None), None, StubPlantDb(care))
    out = svc.monitor_cluster(cid, no_sync=True)
    (row,) = out["sensors"]
    assert (row["target_min"], row["target_max"], row["status"]) == (*band, status)
    assert out["needs_water"] == (["Band Probe (unknown): 33%"] if status == "dry" else [])


def test_monitor_latest_soil_skips_missing_values_and_reports_no_data(db):
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
    svc = IrrigationService(db, None, StubSync(None), None, StubPlantDb({}))
    rows = svc.monitor_cluster(cid, no_sync=True)["sensors"]
    assert [(r["sensor_name"], r["soil_moisture"], r["status"]) for r in rows] == [
        ("P1", 52.0, "ok"),
        ("P2", None, "no_data"),
    ]


# ── check_all_clusters: the failure is logged from inside the except ──────────


def test_check_all_failure_log_carries_the_exception(db, monkeypatch, caplog):
    cid = db.add_cluster("Crashy")
    db.session.commit()

    class Boom(RuntimeError):
        pass

    def explode(self, cluster_id):
        raise Boom("kaput")

    monkeypatch.setattr(IrrigationService, "check_cluster", explode)
    svc = IrrigationService(db, None, StubSync(None), None, plant_database())
    with caplog.at_level(logging.ERROR, logger=IRRIGATION_LOGGER):
        results = svc.check_all_clusters()
    assert results == [
        {"cluster_id": cid, "cluster_name": "Crashy", "action": "error", "notes": "check failed: Boom('kaput')"}
    ]
    (record,) = caplog.records
    assert (record.levelname, record.getMessage(), record.exc_info[0]) == (
        "ERROR",
        f"Check failed for cluster {cid}",
        Boom,
    )


# ── handle_watcher_interrupted: activity write failure is swallowed ────────────


@pytest.mark.parametrize(("triggered_by", "stopped"), [("auto", True), ("manual", False)])
def test_watcher_shutdown_activity_failure_is_logged_not_raised(db, pump, monkeypatch, caplog, triggered_by, stopped):
    def broken(self, **kwargs):
        raise RuntimeError("activity table locked")

    monkeypatch.setattr(IrrigationRepository, "add_activity_event", broken)
    irrigator = db.get_irrigator(pump.irrigator_id)
    with caplog.at_level(logging.ERROR, logger=IRRIGATION_LOGGER):
        assert (
            handle_watcher_interrupted(
                db, pump.registry, irrigator, triggered_by=triggered_by, started_at=FROZEN_TS - 30
            )
            is stopped
        )
    (record,) = [r for r in caplog.records if r.levelname == "ERROR"]
    assert record.getMessage() == f"Failed to record watcher-shutdown activity for irrigator {pump.irrigator_id}"
    assert record.exc_info is not None


# ── schedule_pump_watcher: schedule-time captures vs run-time reads ───────────


@pytest.fixture
def watcher_env(monkeypatch, frozen_clock):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    repo = IrrigationRepository(session)
    cid = repo.add_cluster("Watch Gap")
    iid = repo.add_irrigator(
        cluster_id=cid, tuya_device_id="wg", name="Watch Gap Pump", irrigator_type="tuya_cloud", config={}
    )
    session.commit()
    session.close()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", FakeIrrigatorAdapter)
    settings = SimpleNamespace(
        pump_watcher_enabled=True,
        pump_watcher_poll_seconds=0.7,
        pump_watcher_warmup_seconds=3.0,
        pump_watcher_max_read_failures=4,
    )
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, device_registry=registry, session_factory=factory))
    fake = FakeScheduler()

    def original_wait(_s):
        return False

    def original_stop():
        return False

    monkeypatch.setattr(sched, "scheduler", fake)
    monkeypatch.setattr(sched, "_app", app)
    monkeypatch.setattr(sched, "shutdown_requested", original_stop)
    monkeypatch.setattr(sched, "wait_for_shutdown", original_wait)
    built: list[dict] = []

    class SpyWatcher(PumpWatcherService):
        def __init__(self, repo, registry, **kwargs):
            built.append({"repo": repo, "registry": registry, **kwargs})
            super().__init__(repo, registry, **kwargs)

        def watch(self, irrigator, duration_seconds, *, started_at=None):
            built[-1]["watched"] = (irrigator.id, duration_seconds, started_at)
            return {"outcome": "completed"}

    monkeypatch.setattr(pump_watcher_module, "PumpWatcherService", SpyWatcher)
    yield SimpleNamespace(
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


def test_watcher_job_without_a_health_monitor_passes_none(watcher_env):
    assert not hasattr(watcher_env.app.state, "health_monitor")
    assert schedule_pump_watcher(watcher_env.irrigator_id, 2, FROZEN_TS) is True
    watcher_env.scheduler.jobs[0]["func"]()
    (built,) = watcher_env.built
    assert built["monitor"] is None
    assert built["watched"] == (watcher_env.irrigator_id, 120, FROZEN_TS)


def test_watcher_tuning_is_read_when_the_job_runs(watcher_env):
    schedule_pump_watcher(watcher_env.irrigator_id, 2, FROZEN_TS)
    watcher_env.settings.pump_watcher_poll_seconds = 9.5
    watcher_env.settings.pump_watcher_warmup_seconds = 1.25
    watcher_env.settings.pump_watcher_max_read_failures = 11
    watcher_env.settings.pump_watcher_enabled = False  # only consulted at schedule time
    watcher_env.scheduler.jobs[0]["func"]()
    (built,) = watcher_env.built
    assert (built["poll_seconds"], built["warmup_seconds"], built["max_read_failures"]) == (9.5, 1.25, 11)


def test_watcher_settings_removed_after_scheduling_fall_back_to_defaults(watcher_env):
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


def test_watcher_monitor_is_read_when_the_job_runs(watcher_env):
    schedule_pump_watcher(watcher_env.irrigator_id, 2, FROZEN_TS)
    bound: list = []
    watcher_env.app.state.health_monitor = SimpleNamespace(bind_repo=bound.append)
    watcher_env.scheduler.jobs[0]["func"]()
    (built,) = watcher_env.built
    assert built["monitor"] is watcher_env.app.state.health_monitor
    assert bound == [built["repo"]]


def test_watcher_job_keeps_the_app_and_hooks_captured_at_schedule_time(watcher_env, monkeypatch):
    schedule_pump_watcher(watcher_env.irrigator_id, 2, FROZEN_TS)
    other_registry = DeviceRegistry()
    opened: list = []
    other_app = SimpleNamespace(
        state=SimpleNamespace(settings=None, device_registry=other_registry, session_factory=lambda: opened.append(1))
    )
    monkeypatch.setattr(sched, "_app", other_app)
    monkeypatch.setattr(sched, "wait_for_shutdown", lambda s: True)
    monkeypatch.setattr(sched, "shutdown_requested", lambda: True)
    watcher_env.app.state.device_registry = other_registry  # the registry object was captured too
    watcher_env.scheduler.jobs[0]["func"]()
    (built,) = watcher_env.built
    assert opened == []
    assert built["registry"] is watcher_env.registry
    assert built["sleep"] is watcher_env.wait and built["stop_requested"] is watcher_env.stop
    assert (built["poll_seconds"], built["max_read_failures"]) == (0.7, 4)


# ── rearm_leak_checks: a failure mid-scan keeps the partial count ─────────────


def test_rearm_failure_mid_scan_keeps_partial_count_and_logs_both_lines(monkeypatch, frozen_clock, caplog):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    repo = IrrigationRepository(session)
    ids = []
    for n in range(2):
        cid = repo.add_cluster(f"Rearm {n}")
        iid = repo.add_irrigator(
            cluster_id=cid, tuya_device_id=f"rg{n}", name=f"P{n}", irrigator_type="tuya_cloud", config={}
        )
        repo.add_irrigation_event(
            irrigator_id=iid, action="start", triggered_by="auto", duration_minutes=3, timestamp=FROZEN_TS - 60
        )
        ids.append(cid)
    session.commit()
    session.close()
    fake = FakeScheduler()
    monkeypatch.setattr(sched, "scheduler", fake)
    monkeypatch.setattr(sched, "_app", SimpleNamespace(state=SimpleNamespace(session_factory=factory)))
    real = IrrigationRepository.get_recent_events
    seen: list[int] = []

    def flaky(self, irrigator_id, hours=24):
        seen.append(irrigator_id)
        if len(seen) == 2:
            raise RuntimeError("db gone")
        return real(self, irrigator_id, hours=hours)

    monkeypatch.setattr(IrrigationRepository, "get_recent_events", flaky)
    with caplog.at_level(logging.INFO, logger=IRRIGATION_LOGGER):
        assert irrigation_mod.rearm_leak_checks() == 1
    assert [j["id"] for j in fake.jobs] == [f"leak-check-{ids[0]}-{FROZEN_TS - 60}"]
    assert [(r.levelname, r.getMessage(), r.exc_info is not None) for r in caplog.records] == [
        ("ERROR", "Re-arming leak checks after restart failed", True),
        ("INFO", "Re-armed 1 post-irrigation leak check(s) after restart", False),
    ]
    engine.dispose()


# ── Scheduler job bodies: session scaffolding ─────────────────────────────────


@pytest.fixture
def p(clean_env, frozen_clock):
    pipeline = Pipeline()
    yield pipeline
    pipeline.close()


class SpySession:
    """Wraps a real session and logs commit / rollback / close in call order."""

    def __init__(self, real: Session, log: list[str]) -> None:
        self._real = real
        self._log = log

    def commit(self):
        self._log.append("commit")
        return self._real.commit()

    def rollback(self):
        self._log.append("rollback")
        return self._real.rollback()

    def close(self):
        self._log.append("close")
        return self._real.close()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _spy_sessions(monkeypatch, app) -> list[str]:
    log: list[str] = []
    real_factory = app.state.session_factory

    def factory():
        log.append("open")
        return SpySession(real_factory(), log)

    monkeypatch.setattr(app.state, "session_factory", factory)
    return log


class StubMonitor:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.repos: list = []

    def bind_repo(self, repo):
        self.log.append("bind")
        self.repos.append(repo)

    def poll_all(self):
        self.log.append("poll")

    def is_actuation_blocked(self, irrigator):
        self.log.append(f"blocked?{irrigator.id}")
        return False, []


JOBS = {
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


def _wire_job(p, monkeypatch, log: list[str]) -> StubMonitor:
    from server.test_contract_pipeline import RecordingGateway

    p.app.state.device_gateway = RecordingGateway()
    monitor = StubMonitor(log)
    p.app.state.health_monitor = monitor
    return monitor


@pytest.mark.parametrize("job", list(JOBS))
def test_job_success_commits_then_closes(p, monkeypatch, job):
    func_name, _, _ = JOBS[job]
    log = _spy_sessions(monkeypatch, p.app)
    _wire_job(p, monkeypatch, log)
    getattr(sched, func_name)()
    assert [e for e in log if e in ("open", "commit", "rollback", "close")] == ["open", "commit", "close"]


@pytest.mark.parametrize("job", list(JOBS))
def test_job_failure_rolls_back_logs_with_traceback_and_closes(p, monkeypatch, caplog, job):
    func_name, target, message = JOBS[job]
    log = _spy_sessions(monkeypatch, p.app)
    monitor = _wire_job(p, monkeypatch, log)

    class JobBoom(RuntimeError):
        pass

    def explode(*_a, **_kw):
        raise JobBoom(job)

    if target is None:
        monkeypatch.setattr(monitor, "poll_all", explode)
    else:
        monkeypatch.setattr(target, explode)
    with caplog.at_level(logging.ERROR, logger=SCHEDULER_LOGGER):
        getattr(sched, func_name)()  # must not raise
    assert [e for e in log if e in ("open", "commit", "rollback", "close")] == ["open", "rollback", "close"]
    records = [r for r in caplog.records if r.name == SCHEDULER_LOGGER]
    assert [(r.levelname, r.getMessage()) for r in records] == [("ERROR", message)]
    assert records[0].exc_info[0] is JobBoom


@pytest.mark.parametrize("job", ["snapshot", "check", "anomaly", "monitor"])
def test_session_factory_failure_escapes_the_job(p, monkeypatch, job):
    func_name, _, _ = JOBS[job]
    _wire_job(p, monkeypatch, [])

    def broken_factory():
        raise ConnectionError("db file locked")

    monkeypatch.setattr(p.app.state, "session_factory", broken_factory)
    with pytest.raises(ConnectionError, match="db file locked"):
        getattr(sched, func_name)()


@pytest.mark.parametrize(
    ("job", "escapes"),
    [("sync", False), ("snapshot", True), ("check", True), ("anomaly", True), ("monitor", False)],
)
def test_jobs_without_an_app(p, monkeypatch, job, escapes):
    func_name, _, _ = JOBS[job]
    monkeypatch.setattr(sched, "_app", None)
    if escapes:
        with pytest.raises(AttributeError):
            getattr(sched, func_name)()
    else:
        assert getattr(sched, func_name)() is None


def test_check_job_binds_the_monitor_to_its_repo_before_the_pipeline_asks_it(p, monkeypatch):
    ids = p.seed_cluster(soil=35.0)
    p.app.state.device_registry = p.wiring.registry
    log: list[str] = []
    monitor = StubMonitor(log)
    p.app.state.health_monitor = monitor
    built: list = []
    real_init = IrrigationService.__init__

    def spy_init(self, *args, **kwargs):
        log.append("service")
        built.append(kwargs)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(IrrigationService, "__init__", spy_init)
    sched._check_job()
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
    assert kwargs["sync_service"]._repo is kwargs["repo"]
    assert [c[0] for c in p.wiring.irrigator.calls] == ["start"]


# ── M-pre survivors (refactor/wp-handoff/WP7.md) ──────────────────────────────


def test_job_skip_guards_log_at_debug(p, caplog):
    """M-pre survivors: the two pre-session guards each log one debug line."""
    assert getattr(p.app.state, "device_gateway", None) is None
    assert getattr(p.app.state, "health_monitor", None) is None
    with caplog.at_level(logging.DEBUG, logger=SCHEDULER_LOGGER):
        sched._sync_job()
        sched._health_monitor_job()
    assert [(r.levelname, r.getMessage()) for r in caplog.records if r.name == SCHEDULER_LOGGER] == [
        ("DEBUG", "Sync job skipped: no Tuya credentials"),
        ("DEBUG", "Health monitor job skipped: no monitor wired"),
    ]


@pytest.mark.parametrize("outcome", ["completed", "interrupted", "boom"])
def test_watcher_job_session_commit_rollback_close_order(watcher_env, monkeypatch, outcome):
    """M-pre survivor: the watcher job rolls back on failure, commits only after an interrupt, always closes."""
    log: list[str] = []
    real_factory = watcher_env.factory

    def factory():
        log.append("open")
        return SpySession(real_factory(), log)

    watcher_env.app.state.session_factory = factory

    def watch(self, irrigator, duration_seconds, *, started_at=None):
        if outcome == "boom":
            raise RuntimeError("watch exploded")
        return {"outcome": outcome}

    monkeypatch.setattr(pump_watcher_module.PumpWatcherService, "watch", watch)  # the spy subclass
    monkeypatch.setattr(irrigation_mod, "handle_watcher_interrupted", lambda *a, **kw: log.append("interrupted"))
    schedule_pump_watcher(watcher_env.irrigator_id, 2, FROZEN_TS, triggered_by="manual")
    watcher_env.scheduler.jobs[0]["func"]()
    assert (
        log
        == {
            "completed": ["open", "close"],
            "interrupted": ["open", "interrupted", "commit", "close"],
            "boom": ["open", "rollback", "close"],
        }[outcome]
    )


def test_leak_check_done_scans_at_most_500_leak_rows(db, monkeypatch):
    """M-post guard for the ``limit=500`` literal (bug B-23 neighbourhood: older markers are not seen)."""
    seen: list[dict] = []

    def spy(self, **kwargs):
        seen.append(kwargs)
        return []

    monkeypatch.setattr(IrrigationRepository, "list_activity_events", spy)
    assert irrigation_mod._leak_check_done(db, 7, FROZEN_TS) is False
    assert seen == [{"entity_type": "cluster", "entity_id": 7, "source": "leak", "limit": 500}]


def test_leak_check_done_ignores_other_codes_and_empty_payloads(db):
    """M-pre survivor: only ``leak_check`` / ``leak_hold`` rows with a payload count as a completed check."""
    cid = db.add_cluster("Done Gap")
    for code, payload in (("leak_suspect", {"started_at": FROZEN_TS}), ("leak_check", None)):
        db.add_activity_event(
            source="leak", entity_type="cluster", entity_id=cid, code=code, message="m", payload=payload
        )
    assert irrigation_mod._leak_check_done(db, cid, FROZEN_TS) is False
    db.add_activity_event(
        source="leak",
        entity_type="cluster",
        entity_id=cid,
        code="leak_hold",
        message="m",
        payload={"started_at": FROZEN_TS},
    )
    assert irrigation_mod._leak_check_done(db, cid, FROZEN_TS) is True


@pytest.mark.parametrize("fail", [False, True])
def test_rearm_always_closes_its_session(monkeypatch, frozen_clock, fail):
    """M-pre survivor: ``rearm_leak_checks`` closes its session on success and after a failure."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    real_factory = sessionmaker(bind=engine)
    log: list[str] = []

    def factory():
        log.append("open")
        return SpySession(real_factory(), log)

    monkeypatch.setattr(sched, "scheduler", FakeScheduler())
    monkeypatch.setattr(sched, "_app", SimpleNamespace(state=SimpleNamespace(session_factory=factory)))
    if fail:
        monkeypatch.setattr(IrrigationRepository, "list_all_irrigators", lambda self: 1 / 0)
    assert irrigation_mod.rearm_leak_checks() == 0
    assert log == ["open", "close"]
    engine.dispose()


def test_health_block_names_the_first_blocking_alarm(db, pump, monkeypatch):
    """M-pre survivor: the device-health gate reports ``blocking_alarms[0]`` as the primary alarm."""
    from greenhouse_core.devices.health import HealthAlarm

    class Blocking:
        def is_actuation_blocked(self, irrigator):
            return True, [HealthAlarm.NO_WATER, HealthAlarm.DEVICE_OFFLINE]

    _patch_decide(monkeypatch, _decision)
    svc = IrrigationService(
        db, pump.registry, StubSync(None), StubWeather(None), plant_database(), health_monitor=Blocking()
    )
    result = svc.run_irrigation_pipeline(pump.cluster_id, no_sync=True)
    assert pump.adapter.calls == []
    assert (result["action"], result["reason"], result["blocking_alarms"]) == (
        "skip",
        "soil low; Actuation blocked by device health: no_water on 'Gap Pump'",
        ["no_water", "device_offline"],
    )
    assert [r["code"] for r in result["reasons"]] == ["sensor_dry", "device_no_water"]
    (activity,) = _rows(db, ActivityEvent)
    assert (activity.code, activity.severity) == ("decision_skip", "warning")


@pytest.mark.parametrize(("no_sync", "calls"), [(False, 1), (True, 0)])
def test_monitor_syncs_unless_told_not_to(db, no_sync, calls):
    """M-pre survivor: ``monitor_cluster`` refreshes the cluster through the sync service unless ``no_sync``."""
    cid = db.add_cluster("Sync Gap")
    sync = StubSync(None)
    svc = IrrigationService(db, None, sync, None, StubPlantDb({}))
    assert svc.monitor_cluster(cid, no_sync=no_sync) == {"cluster_name": "Sync Gap", "sensors": [], "needs_water": []}
    assert sync.calls == [cid] * calls


def test_monitor_looks_back_two_hours(db):
    """M-pre survivor: monitor reads the last 2 h of readings (a 1.5 h-old sample counts, a 2.5 h-old one does not)."""
    cid = db.add_cluster("Lookback Gap")
    fresh = db.add_sensor(
        cluster_id=cid, tuya_device_id="fake_tuya_sensor_lb01", name="F", sensor_type="soil", config={}
    )
    stale = db.add_sensor(
        cluster_id=cid, tuya_device_id="fake_tuya_sensor_lb02", name="S", sensor_type="soil", config={}
    )
    db.add_sensor_reading(sensor_id=fresh, timestamp=FROZEN_TS - 5400, soil_moisture=50.0)
    db.add_sensor_reading(sensor_id=stale, timestamp=FROZEN_TS - 9000, soil_moisture=50.0)
    svc = IrrigationService(db, None, StubSync(None), None, StubPlantDb({}))
    rows = svc.monitor_cluster(cid, no_sync=True)["sensors"]
    assert [(r["sensor_name"], r["soil_moisture"], r["status"]) for r in rows] == [
        ("F", 50.0, "ok"),
        ("S", None, "no_data"),
    ]


@pytest.mark.parametrize(
    ("soil", "status"), [(5.0, "dry"), (4.9, "very_dry"), (19.9, "dry"), (20.0, "ok"), (40.0, "ok"), (40.1, "wet")]
)
def test_monitor_status_ladder_edges(db, soil, status):
    """M-pre survivor: the 5-way ladder is strict ``<`` / ``>`` at every edge (band 20-30)."""
    cid = db.add_cluster("Ladder Gap")
    sid = db.add_sensor(cluster_id=cid, tuya_device_id="fake_tuya_sensor_lad1", name="L", sensor_type="soil", config={})
    db.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=soil)
    svc = IrrigationService(db, None, StubSync(None), None, StubPlantDb({"soil_moisture_target": "20-30"}))
    (row,) = svc.monitor_cluster(cid, no_sync=True)["sensors"]
    assert row["status"] == status


@pytest.mark.parametrize("branch", ["monitored", "auto_run_off", "pipeline"])
def test_check_cluster_call_order_and_result_shape(db, monkeypatch, branch):
    """M-pre survivor: learning → maintenance → (config) → pipeline/monitor → ``sync_cluster_alerts`` → result."""
    cid = db.add_cluster("Order Gap")
    if branch != "monitored":
        db.add_irrigator(
            cluster_id=cid, tuya_device_id="fake_tuya_device_ord1", name="O", irrigator_type="tuya_cloud", config={}
        )
    if branch == "auto_run_off":
        db.set_irrigation_config(cid, auto_run=False)
    db.session.commit()
    log: list[str] = []
    monkeypatch.setattr(irrigation_mod, "collect_learning_alerts", lambda *a: log.append("learning") or [{"a": 1}])
    monkeypatch.setattr(
        irrigation_mod, "collect_maintenance_alerts", lambda *a: log.append("maintenance") or [{"m": 1}]
    )
    monkeypatch.setattr(
        irrigation_mod, "sync_cluster_alerts", lambda repo, c, pdb, *, notifier: log.append(f"sync:{c}:{notifier}")
    )
    monkeypatch.setattr(
        IrrigationService,
        "run_irrigation_pipeline",
        lambda self, c: log.append("pipeline") or {"action": "skip", "reason": "why", "confidence": 0.5},
    )
    monkeypatch.setattr(
        IrrigationService, "monitor_cluster", lambda self, c: log.append("monitor") or {"needs_water": ["x"]}
    )
    svc = IrrigationService(db, None, StubSync(None), None, StubPlantDb({}))
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
