"""Characterization: pump-watcher scheduling and the shutdown hand-off (gaps only).

``test_pump_watcher.py`` pins the watch loop and ``test_scheduler_shutdown.py`` pins
prompt exit (subprocess) and ``handle_watcher_interrupted`` at a coarse level. This
module pins what neither does, in-process:

- ``schedule_pump_watcher``: every "skip" branch, the exact APScheduler job it adds
  (trigger, run date, id, name, ``replace_existing``) and the watcher it builds
  (settings → poll/warm-up/failure budget, defaults without settings, shutdown
  hooks, monitor re-bound to the job's repo);
- the job body when shutdown interrupts the watch: auto cycles are **stopped**
  (``IrrigationEvent(action="stop", triggered_by="shutdown")``), manual cycles are
  **left on the device timer** — both logged and recorded as a
  ``pump_watcher_shutdown`` activity with exact message/payload, and committed;
- exact interrupted-result shape and dry-run trip side effects (notes, message,
  payload) under a frozen clock.

The process-wide scheduler is never started: ``greenhouse_server.scheduler``'s
``scheduler`` / ``_app`` / shutdown hooks are replaced by fakes for each test.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fake_devices import FakeIrrigatorAdapter
from golden import FROZEN_TS
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.models import ActivityEvent, Base, IrrigationEvent
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server import scheduler as scheduler_module
from greenhouse_server.services import pump_watcher as pump_watcher_module
from greenhouse_server.services.irrigation import WATCHER_SHUTDOWN_ACTIVITY_CODE, schedule_pump_watcher
from greenhouse_server.services.pump_watcher import PumpWatcherService

STARTED_AT = FROZEN_TS - 42


class FakeScheduler:
    def __init__(self, *, running: bool = True, fail: bool = False) -> None:
        self.running = running
        self.fail = fail
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, **kwargs):
        if self.fail:
            raise RuntimeError("jobstore down")
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})


class RecordingMonitor:
    def __init__(self) -> None:
        self.bound: list = []

    def bind_repo(self, repo) -> None:
        self.bound.append(repo)


@pytest.fixture
def env(monkeypatch, frozen_clock):
    """A shared in-memory DB, one irrigator, a fake adapter/registry and a fake ``_app``."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    repo = IrrigationRepository(session)
    cid = repo.add_cluster("Watch Cluster")
    iid = repo.add_irrigator(
        cluster_id=cid, tuya_device_id="wp", name="Watch Pump", irrigator_type="tuya_cloud", config={}
    )
    session.commit()
    session.close()

    adapter = FakeIrrigatorAdapter()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda: adapter)
    settings = SimpleNamespace(
        pump_watcher_enabled=True,
        pump_watcher_poll_seconds=0.7,
        pump_watcher_warmup_seconds=3.0,
        pump_watcher_max_read_failures=4,
    )
    monitor = RecordingMonitor()
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=settings, device_registry=registry, session_factory=factory, health_monitor=monitor
        )
    )
    fake_scheduler = FakeScheduler()
    shutdown = {"requested": True}
    waits: list[float] = []
    monkeypatch.setattr(scheduler_module, "scheduler", fake_scheduler)
    monkeypatch.setattr(scheduler_module, "_app", app)
    monkeypatch.setattr(scheduler_module, "shutdown_requested", lambda: shutdown["requested"])
    monkeypatch.setattr(scheduler_module, "wait_for_shutdown", lambda s: waits.append(s) or False)

    built: list[dict] = []

    class SpyWatcher(PumpWatcherService):
        def __init__(self, repo, registry, **kwargs):
            built.append({"repo": repo, "registry": registry, **kwargs})
            super().__init__(repo, registry, **kwargs)

    monkeypatch.setattr(pump_watcher_module, "PumpWatcherService", SpyWatcher)
    yield SimpleNamespace(
        engine=engine,
        factory=factory,
        irrigator_id=iid,
        cluster_id=cid,
        adapter=adapter,
        registry=registry,
        settings=settings,
        monitor=monitor,
        app=app,
        scheduler=fake_scheduler,
        shutdown=shutdown,
        built=built,
    )
    engine.dispose()


def _db_state(env) -> dict:
    session = env.factory()
    try:
        events = [
            {
                "action": e.action,
                "triggered_by": e.triggered_by,
                "duration_minutes": e.duration_minutes,
                "notes": e.notes,
                "timestamp": e.timestamp,
            }
            for e in session.query(IrrigationEvent).order_by(IrrigationEvent.id)
        ]
        activity = [
            {
                "source": a.source,
                "entity_type": a.entity_type,
                "entity_id": a.entity_id,
                "code": a.code,
                "message": a.message,
                "severity": a.severity,
                "payload": json.loads(a.payload_json) if a.payload_json else None,
            }
            for a in session.query(ActivityEvent).order_by(ActivityEvent.id)
        ]
        return {"events": events, "activity": activity}
    finally:
        session.close()


# ── schedule_pump_watcher: skip branches and the job it adds ──────────────────


@pytest.mark.parametrize("minutes", [0, -5])
def test_non_positive_duration_is_not_scheduled(env, minutes):
    assert schedule_pump_watcher(env.irrigator_id, minutes, STARTED_AT) is False
    assert env.scheduler.jobs == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda env, mp: setattr(env.scheduler, "running", False),
        lambda env, mp: mp.setattr(scheduler_module, "_app", None),
        lambda env, mp: setattr(env.settings, "pump_watcher_enabled", False),
        lambda env, mp: setattr(env.app.state, "device_registry", None),
    ],
    ids=["scheduler-stopped", "no-app", "watcher-disabled", "no-registry"],
)
def test_unavailable_watcher_is_not_scheduled(env, monkeypatch, mutate):
    mutate(env, monkeypatch)
    assert schedule_pump_watcher(env.irrigator_id, 5, STARTED_AT) is False
    assert env.scheduler.jobs == []


def test_add_job_failure_returns_false_and_logs_at_debug(env, caplog):
    env.scheduler.fail = True
    with caplog.at_level(logging.DEBUG, logger="greenhouse_server.services.irrigation"):
        assert schedule_pump_watcher(env.irrigator_id, 5, STARTED_AT) is False
    assert caplog.messages == [f"Could not schedule pump watcher for irrigator {env.irrigator_id}"]


def test_scheduled_job_shape(env):
    assert schedule_pump_watcher(env.irrigator_id, 5, STARTED_AT, triggered_by="manual") is True
    (job,) = env.scheduler.jobs
    assert {k: v for k, v in job.items() if k != "func"} == {
        "trigger": "date",
        "run_date": datetime.fromtimestamp(STARTED_AT, tz=UTC),
        "id": f"pump-watcher-{env.irrigator_id}-{STARTED_AT}",
        "name": f"Pump watcher irrigator {env.irrigator_id}",
        "replace_existing": True,
    }
    assert callable(job["func"])


def test_missing_settings_still_schedules_and_uses_watcher_defaults(env):
    env.app.state.settings = None
    assert schedule_pump_watcher(env.irrigator_id, 2, STARTED_AT) is True
    env.scheduler.jobs[0]["func"]()
    (built,) = env.built
    assert (built["poll_seconds"], built["warmup_seconds"], built["max_read_failures"]) == (2.0, 5.0, 5)


# ── The job body ──────────────────────────────────────────────────────────────


def test_job_builds_watcher_from_settings_with_shutdown_hooks(env):
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT)
    env.scheduler.jobs[0]["func"]()
    (built,) = env.built
    assert built["registry"] is env.registry
    assert (built["poll_seconds"], built["warmup_seconds"], built["max_read_failures"]) == (0.7, 3.0, 4)
    assert built["monitor"] is env.monitor
    assert env.monitor.bound == [built["repo"]]  # monitor re-bound to the job's own session
    assert built["stop_requested"] is scheduler_module.shutdown_requested
    assert built["sleep"] is scheduler_module.wait_for_shutdown


def test_shutdown_stops_auto_cycle_and_records_it(env, caplog):
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT, triggered_by="auto")
    with caplog.at_level(logging.INFO, logger="greenhouse_server.services.irrigation"):
        env.scheduler.jobs[0]["func"]()
    assert [c[0] for c in env.adapter.calls] == ["stop"]  # no read_health: interrupted before the first poll
    assert caplog.messages == [
        f"Server shutting down mid-irrigation: stopped auto cycle on irrigator {env.irrigator_id} "
        "(dry-run watcher can no longer protect it)"
    ]
    assert _db_state(env) == {
        "events": [
            {
                "action": "stop",
                "triggered_by": "shutdown",
                "duration_minutes": None,
                "notes": "server shutdown: dry-run watcher interrupted, auto cycle stopped",
                "timestamp": FROZEN_TS,
            }
        ],
        "activity": [
            {
                "source": "irrigation",
                "entity_type": "irrigator",
                "entity_id": env.irrigator_id,
                "code": WATCHER_SHUTDOWN_ACTIVITY_CODE,
                "message": "Server shutdown stopped the auto irrigation on 'Watch Pump' (watcher interrupted)",
                "severity": "warning",
                "payload": {"triggered_by": "auto", "started_at": STARTED_AT, "stopped": True},
            }
        ],
    }


def test_shutdown_leaves_manual_cycle_on_device_timer_and_records_it(env, caplog):
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT, triggered_by="manual")
    with caplog.at_level(logging.INFO, logger="greenhouse_server.services.irrigation"):
        env.scheduler.jobs[0]["func"]()
    assert env.adapter.calls == []
    assert caplog.messages == [
        f"Server shutting down mid-irrigation: manual cycle on irrigator {env.irrigator_id} left running "
        "unprotected (no dry-run watcher) until the device timer ends it"
    ]
    assert _db_state(env) == {
        "events": [],
        "activity": [
            {
                "source": "irrigation",
                "entity_type": "irrigator",
                "entity_id": env.irrigator_id,
                "code": WATCHER_SHUTDOWN_ACTIVITY_CODE,
                "message": "Server shutdown: manual irrigation on 'Watch Pump' continues without dry-run "
                "protection until the device timer ends it",
                "severity": "warning",
                "payload": {"triggered_by": "manual", "started_at": STARTED_AT, "stopped": False},
            }
        ],
    }


@pytest.mark.parametrize(
    ("stop", "stop_msg"),
    [
        ((False, "device unreachable"), "device unreachable"),
        (ConnectionError("socket closed"), "adapter.stop raised: socket closed"),
    ],
    ids=["stop-returns-false", "stop-raises"],
)
def test_shutdown_failed_auto_stop_is_logged_and_recorded(env, caplog, stop, stop_msg):
    if isinstance(stop, BaseException):

        def _raise(_irrigator):
            raise stop

        env.adapter.stop = _raise  # type: ignore[method-assign]
    else:
        env.adapter.stop_result = stop
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT)
    with caplog.at_level(logging.INFO, logger="greenhouse_server.services.irrigation"):
        env.scheduler.jobs[0]["func"]()
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        (
            "ERROR",
            f"Server shutting down mid-irrigation: FAILED to stop auto cycle on irrigator {env.irrigator_id} "
            f"({stop_msg}) — it continues unprotected until the device timer ends it",
        )
    ]
    state = _db_state(env)
    assert state["events"] == []
    assert [(a["message"], a["payload"]) for a in state["activity"]] == [
        (
            f"Server shutdown could not stop the auto irrigation on 'Watch Pump' ({stop_msg}); "
            "it continues without dry-run protection",
            {"triggered_by": "auto", "started_at": STARTED_AT, "stopped": False},
        )
    ]


def test_completed_watch_records_nothing(env, monkeypatch):
    env.shutdown["requested"] = False
    monkeypatch.setattr(PumpWatcherService, "watch", lambda self, irr, secs, started_at=None: {"outcome": "completed"})
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT)
    env.scheduler.jobs[0]["func"]()
    assert env.adapter.calls == []
    assert _db_state(env) == {"events": [], "activity": []}


def test_deleted_irrigator_ends_the_job_quietly(env):
    schedule_pump_watcher(env.irrigator_id + 100, 3, STARTED_AT)
    env.scheduler.jobs[0]["func"]()
    assert env.built == [] and env.adapter.calls == []


def test_job_failure_is_rolled_back_and_logged(env, monkeypatch, caplog):
    def _boom(self, irr, secs, started_at=None):
        raise RuntimeError("watch exploded")

    monkeypatch.setattr(PumpWatcherService, "watch", _boom)
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.services.irrigation"):
        env.scheduler.jobs[0]["func"]()  # must not raise
    assert caplog.messages == [f"Pump watcher job failed for irrigator {env.irrigator_id}"]


# ── Watcher result shapes and trip side effects (frozen clock) ────────────────


@pytest.fixture
def plain(frozen_clock):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repo = IrrigationRepository(session)
    cid = repo.add_cluster("Trip Cluster")
    iid = repo.add_irrigator(
        cluster_id=cid, tuya_device_id="tp", name="Trip Pump", irrigator_type="tuya_cloud", config={}
    )
    session.commit()
    adapter = FakeIrrigatorAdapter()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda: adapter)
    yield SimpleNamespace(
        repo=repo, irrigator=repo.get_irrigator(iid), cluster_id=cid, adapter=adapter, registry=registry
    )
    session.close()
    engine.dispose()


def test_interrupted_result_shape_after_one_poll(plain):
    ticks = iter([100.0, 100.0, 100.0, 101.5])  # deadline, warm-up, loop "now", interrupted elapsed
    flag = {"stop": False}

    def _sleep(_s):
        flag["stop"] = True

    watcher = PumpWatcherService(
        plain.repo,
        plain.registry,
        clock=lambda: next(ticks),
        sleep=_sleep,
        stop_requested=lambda: flag["stop"],
    )
    assert watcher.watch(plain.irrigator, 3600) == {
        "outcome": "interrupted",
        "polls": 1,
        "read_failures": 0,
        "alarm_raw": None,
        "elapsed_seconds": 1.5,
    }
    assert plain.adapter.calls == [("read_health", plain.irrigator.id)]


def test_trip_side_effects_are_exact(plain):
    plain.adapter.set_health(
        DeviceHealthState(
            observed_at=FROZEN_TS,
            alarms=frozenset({HealthAlarm.NO_WATER}),
            raw={"alarm_raw": 1, "source": "local"},
        )
    )
    watcher = PumpWatcherService(plain.repo, plain.registry, warmup_seconds=0, clock=lambda: 0.0, sleep=lambda s: None)
    result = watcher.watch(plain.irrigator, 60, started_at=STARTED_AT)
    assert result == {"outcome": "tripped", "polls": 1, "read_failures": 0, "alarm_raw": 1, "elapsed_seconds": 0.0}
    assert plain.adapter.calls == [("read_health", plain.irrigator.id), ("stop", plain.irrigator.id)]
    event = plain.repo.session.query(IrrigationEvent).one()
    assert (event.action, event.triggered_by, event.duration_minutes, event.timestamp, event.notes) == (
        "aborted",
        "pump_watcher",
        0,
        FROZEN_TS,
        "pump dry-run detected after ~42s (DP 105=1); stop_ok=True",
    )
    activity = plain.repo.session.query(ActivityEvent).one()
    assert (activity.source, activity.code, activity.severity, activity.message) == (
        "pump",
        "pump_dry_run",
        "critical",
        "Pump dry-run detected on 'Trip Pump' after ~42s — irrigation aborted",
    )
    assert json.loads(activity.payload_json) == {
        "irrigator_id": plain.irrigator.id,
        "irrigator_name": "Trip Pump",
        "cluster_id": plain.cluster_id,
        "alarm_dp": 105,
        "alarm_raw": 1,
        "polls": 1,
        "started_at": STARTED_AT,
        "duration_seconds_requested": 60,
        "elapsed_seconds": 42,
        "stop_ok": True,
        "stop_message": "fake stop ok",
    }


def test_trip_payload_reprs_unserialisable_alarm_value(plain):
    plain.adapter.set_health(
        DeviceHealthState(observed_at=FROZEN_TS, alarms=frozenset({HealthAlarm.NO_WATER}), raw={"alarm_raw": [1, 0]})
    )
    PumpWatcherService(plain.repo, plain.registry, warmup_seconds=0, clock=lambda: 0.0, sleep=lambda s: None).watch(
        plain.irrigator, 60, started_at=STARTED_AT
    )
    activity = plain.repo.session.query(ActivityEvent).one()
    assert json.loads(activity.payload_json)["alarm_raw"] == "[1, 0]"


def test_constructor_clamps_its_knobs(plain):
    watcher = PumpWatcherService(plain.repo, plain.registry, poll_seconds=0, warmup_seconds=-3, max_read_failures=0)
    assert (watcher._poll, watcher._warmup, watcher._max_read_failures) == (0.1, 0.0, 1)


def test_trip_side_effect_failures_are_logged_and_the_stop_still_happens(plain, monkeypatch, caplog):
    """Each trip side effect is isolated: event, activity, monitor and commit failures are all swallowed."""
    plain.adapter.set_health(
        DeviceHealthState(observed_at=FROZEN_TS, alarms=frozenset({HealthAlarm.NO_WATER}), raw={"alarm_raw": 1})
    )

    def _fail(*_args, **_kwargs):
        raise RuntimeError("db gone")

    class _BadMonitor:
        def record(self, *args, **kwargs):
            raise RuntimeError("monitor gone")

    monkeypatch.setattr(plain.repo, "add_irrigation_event", _fail)
    monkeypatch.setattr(plain.repo, "add_activity_event", _fail)
    monkeypatch.setattr(plain.repo.session, "commit", _fail)
    watcher = PumpWatcherService(
        plain.repo, plain.registry, warmup_seconds=0, clock=lambda: 0.0, sleep=lambda s: None, monitor=_BadMonitor()
    )
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.services.pump_watcher"):
        assert watcher.watch(plain.irrigator, 60, started_at=STARTED_AT)["outcome"] == "tripped"
    iid = plain.irrigator.id
    assert plain.adapter.calls == [("read_health", iid), ("stop", iid)]
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        (
            "CRITICAL",
            f"Pump dry-run detected on irrigator {iid} (cluster {plain.cluster_id}) after 1 polls: "
            "alarm_raw=1, stop_ok=True, stop_msg=fake stop ok",
        ),
        ("ERROR", f"Failed to log aborted irrigation event for irrigator {iid}"),
        ("ERROR", f"Failed to log activity event for pump dry-run on irrigator {iid}"),
        ("ERROR", f"Failed to record dry-run state into health monitor for irrigator {iid}"),
        ("ERROR", f"Failed to commit pump dry-run side effects for irrigator {iid}"),
    ]
