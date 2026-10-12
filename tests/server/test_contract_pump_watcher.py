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
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NoReturn, Unpack, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fake_devices import FakeIrrigatorAdapter
from golden import FROZEN_TS
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.models import ActivityEvent, Alert, Base, IrrigationEvent, TriggeredBy
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server import scheduler as scheduler_module
from greenhouse_server.services import pump_watcher as pump_watcher_module
from greenhouse_server.services.pump_watcher import PumpWatcherService
from greenhouse_server.services.pump_watcher_jobs import WATCHER_SHUTDOWN_ACTIVITY_CODE, schedule_pump_watcher

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import time_machine
    from sqlalchemy import Engine

    from greenhouse_core.models import Irrigator
    from greenhouse_server.services.health_monitor import DeviceHealthMonitor
    from greenhouse_server.services.pump_watcher import PumpWatcherOptions

STARTED_AT = FROZEN_TS - 42


class FakeScheduler:
    """Scheduler stand-in that records ``add_job`` calls (or fails them)."""

    def __init__(self, *, running: bool = True, fail: bool = False) -> None:
        """Configure whether the scheduler reports running and whether ``add_job`` fails."""
        self.running = running
        self.fail = fail
        self.jobs: list[dict[str, Any]] = []  # add_job's keyword arguments, as APScheduler takes them

    def add_job(self, func: Callable[[], object], trigger: object, **kwargs: object) -> None:
        """Record the job, or raise like a broken job store."""
        if self.fail:
            msg = "jobstore down"
            raise RuntimeError(msg)
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})


class RecordingMonitor:
    """Health-monitor stand-in that records which repository each view binds to."""

    def __init__(self) -> None:
        """Start with no views bound."""
        self.bound: list[IrrigationRepository] = []

    def for_repo(self, repo: IrrigationRepository) -> RecordingMonitor:
        """Record the repository and act as the view itself."""
        self.bound.append(repo)
        return self


def _watch_db() -> tuple[Engine, sessionmaker[Session], int, int]:
    """A shared in-memory DB holding one cluster with one irrigator: ``(engine, factory, cluster_id, irrigator_id)``."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    repo = IrrigationRepository(session)
    cid = repo.add_cluster("Watch Cluster")
    iid = repo.add_irrigator(
        cluster_id=cid, tuya_device_id="wp", name="Watch Pump", irrigator_type="rainpoint.ik10pw", config={}
    )
    session.commit()
    session.close()
    return engine, factory, cid, iid


def _fake_app(
    factory: sessionmaker[Session],
) -> tuple[FakeIrrigatorAdapter, DeviceRegistry, SimpleNamespace, RecordingMonitor, SimpleNamespace]:
    """Wire a fake adapter, registry and monitor into a stand-in ``_app``.

    Returns ``(adapter, registry, settings, monitor, app)``.
    """
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
    return adapter, registry, settings, monitor, app


@dataclass
class WatchEnv:
    """What the ``env`` fixture wires up."""

    engine: Engine
    factory: sessionmaker[Session]
    irrigator_id: int
    cluster_id: int
    adapter: FakeIrrigatorAdapter
    registry: DeviceRegistry
    settings: SimpleNamespace
    monitor: RecordingMonitor
    app: SimpleNamespace
    scheduler: FakeScheduler
    shutdown: dict[str, bool]
    built: list[dict[str, object]]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, frozen_clock: time_machine.Traveller) -> Iterator[WatchEnv]:
    """A shared in-memory DB, one irrigator, a fake adapter/registry and a fake ``_app``."""
    del frozen_clock  # requested only for its side effect
    engine, factory, cid, iid = _watch_db()
    adapter, registry, settings, monitor, app = _fake_app(factory)
    fake_scheduler = FakeScheduler()
    shutdown = {"requested": True}
    waits: list[float] = []
    monkeypatch.setattr(scheduler_module, "scheduler", fake_scheduler)
    monkeypatch.setattr(scheduler_module, "_app", app)
    monkeypatch.setattr(scheduler_module, "shutdown_requested", lambda: shutdown["requested"])

    def wait_for_shutdown(seconds: float) -> bool:
        waits.append(seconds)
        return False

    monkeypatch.setattr(scheduler_module, "wait_for_shutdown", wait_for_shutdown)

    built: list[dict[str, object]] = []

    class SpyWatcher(PumpWatcherService):
        def __init__(
            self, repo: IrrigationRepository, registry: DeviceRegistry, **kwargs: Unpack[PumpWatcherOptions]
        ) -> None:
            built.append({"repo": repo, "registry": registry, **kwargs})
            super().__init__(repo, registry, **kwargs)

    monkeypatch.setattr(pump_watcher_module, "PumpWatcherService", SpyWatcher)
    yield WatchEnv(
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


def _db_state(env: WatchEnv) -> dict[str, list[dict[str, Any]]]:
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
def test_non_positive_duration_is_not_scheduled(env: WatchEnv, minutes: int) -> None:
    """Non positive duration is not scheduled."""
    assert schedule_pump_watcher(env.irrigator_id, minutes, STARTED_AT) is False
    assert env.scheduler.jobs == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda env, _mp: setattr(env.scheduler, "running", False),
        lambda _env, mp: mp.setattr(scheduler_module, "_app", None),
        lambda env, _mp: setattr(env.settings, "pump_watcher_enabled", False),
        lambda env, _mp: setattr(env.app.state, "device_registry", None),
    ],
    ids=["scheduler-stopped", "no-app", "watcher-disabled", "no-registry"],
)
def test_unavailable_watcher_is_not_scheduled(
    env: WatchEnv, monkeypatch: pytest.MonkeyPatch, mutate: Callable[[WatchEnv, pytest.MonkeyPatch], None]
) -> None:
    """Unavailable watcher is not scheduled."""
    mutate(env, monkeypatch)
    assert schedule_pump_watcher(env.irrigator_id, 5, STARTED_AT) is False
    assert env.scheduler.jobs == []


def test_add_job_failure_returns_false_and_logs_at_debug(env: WatchEnv, caplog: pytest.LogCaptureFixture) -> None:
    """Add job failure returns false and logs at debug."""
    env.scheduler.fail = True
    with caplog.at_level(logging.DEBUG, logger="greenhouse_server.services.irrigation"):
        assert schedule_pump_watcher(env.irrigator_id, 5, STARTED_AT) is False
    assert caplog.messages == [f"Could not schedule pump watcher for irrigator {env.irrigator_id}"]


def test_scheduled_job_shape(env: WatchEnv) -> None:
    """Scheduled job shape."""
    assert schedule_pump_watcher(env.irrigator_id, 5, STARTED_AT, triggered_by=TriggeredBy.MANUAL) is True
    (job,) = env.scheduler.jobs
    assert {k: v for k, v in job.items() if k != "func"} == {
        "trigger": "date",
        "run_date": datetime.fromtimestamp(STARTED_AT, tz=UTC),
        "id": f"pump-watcher-{env.irrigator_id}-{STARTED_AT}",
        "name": f"Pump watcher irrigator {env.irrigator_id}",
        "replace_existing": True,
    }
    assert callable(job["func"])


def test_missing_settings_still_schedules_and_uses_watcher_defaults(env: WatchEnv) -> None:
    """Missing settings still schedules and uses watcher defaults."""
    env.app.state.settings = None
    assert schedule_pump_watcher(env.irrigator_id, 2, STARTED_AT) is True
    env.scheduler.jobs[0]["func"]()
    (built,) = env.built
    assert (built["poll_seconds"], built["warmup_seconds"], built["max_read_failures"]) == (2.0, 5.0, 5)


# ── The job body ──────────────────────────────────────────────────────────────


def test_job_builds_watcher_from_settings_with_shutdown_hooks(env: WatchEnv) -> None:
    """Job builds watcher from settings with shutdown hooks."""
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT)
    env.scheduler.jobs[0]["func"]()
    (built,) = env.built
    assert built["registry"] is env.registry
    assert (built["poll_seconds"], built["warmup_seconds"], built["max_read_failures"]) == (0.7, 3.0, 4)
    assert built["monitor"] is env.monitor
    assert env.monitor.bound == [built["repo"]]  # a monitor view on the job's own session (S6)
    assert built["stop_requested"] is scheduler_module.shutdown_requested
    assert built["sleep"] is scheduler_module.wait_for_shutdown


def test_shutdown_stops_auto_cycle_and_records_it(env: WatchEnv, caplog: pytest.LogCaptureFixture) -> None:
    """Shutdown stops auto cycle and records it."""
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT, triggered_by=TriggeredBy.AUTO)
    with caplog.at_level(logging.INFO, logger="greenhouse_server.services.irrigation"):
        env.scheduler.jobs[0]["func"]()
    assert [c[0] for c in env.adapter.calls] == ["stop"]  # no read_health: interrupted before the first poll
    assert caplog.messages == [
        (
            f"Server shutting down mid-irrigation: stopped auto cycle on irrigator {env.irrigator_id} "
            "(dry-run watcher can no longer protect it)"
        )
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


def test_shutdown_leaves_manual_cycle_on_device_timer_and_records_it(
    env: WatchEnv, caplog: pytest.LogCaptureFixture
) -> None:
    """Shutdown leaves manual cycle on device timer and records it."""
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT, triggered_by=TriggeredBy.MANUAL)
    with caplog.at_level(logging.INFO, logger="greenhouse_server.services.irrigation"):
        env.scheduler.jobs[0]["func"]()
    assert env.adapter.calls == []
    assert caplog.messages == [
        (
            f"Server shutting down mid-irrigation: manual cycle on irrigator {env.irrigator_id} left running "
            "unprotected (no dry-run watcher) until the device timer ends it"
        )
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


def test_shutdown_stops_forced_run_like_an_auto_cycle(env: WatchEnv, caplog: pytest.LogCaptureFixture) -> None:
    """A forced ``/irrigate`` run is recorded ``manual`` but timed by the engine and unwatched: stopped."""
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT, triggered_by=TriggeredBy.MANUAL, forced=True)
    with caplog.at_level(logging.INFO, logger="greenhouse_server.services.irrigation"):
        env.scheduler.jobs[0]["func"]()
    assert [c[0] for c in env.adapter.calls] == ["stop"]
    assert caplog.messages == [
        (
            f"Server shutting down mid-irrigation: stopped forced cycle on irrigator {env.irrigator_id} "
            "(dry-run watcher can no longer protect it)"
        )
    ]
    state = _db_state(env)
    assert [(e["action"], e["triggered_by"], e["notes"]) for e in state["events"]] == [
        ("stop", "shutdown", "server shutdown: dry-run watcher interrupted, forced cycle stopped")
    ]
    (activity,) = state["activity"]
    assert activity["message"] == "Server shutdown stopped the forced irrigation on 'Watch Pump' (watcher interrupted)"
    assert activity["payload"] == {"triggered_by": "manual", "started_at": STARTED_AT, "stopped": True, "forced": True}


@pytest.mark.parametrize(
    ("stop", "stop_msg"),
    [
        ((False, "device unreachable"), "device unreachable"),
        (ConnectionError("socket closed"), "adapter.stop raised: socket closed"),
    ],
    ids=["stop-returns-false", "stop-raises"],
)
def test_shutdown_failed_auto_stop_is_logged_and_recorded(
    env: WatchEnv, caplog: pytest.LogCaptureFixture, stop: tuple[bool, str] | BaseException, stop_msg: str
) -> None:
    """Shutdown failed auto stop is logged and recorded."""
    if isinstance(stop, BaseException):

        def _raise(irrigator: Irrigator) -> tuple[bool, str]:
            del irrigator  # named like adapter.stop's parameter; the stub fails for any device
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
            (
                f"Server shutting down mid-irrigation: FAILED to stop auto cycle on irrigator {env.irrigator_id} "
                f"({stop_msg}) — it continues unprotected until the device timer ends it"
            ),
        )
    ]
    state = _db_state(env)
    assert state["events"] == []
    assert [(a["message"], a["payload"]) for a in state["activity"]] == [
        (
            (
                f"Server shutdown could not stop the auto irrigation on 'Watch Pump' ({stop_msg}); "
                "it continues without dry-run protection"
            ),
            {"triggered_by": "auto", "started_at": STARTED_AT, "stopped": False},
        )
    ]


def test_completed_watch_records_nothing(env: WatchEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    """Completed watch records nothing."""
    env.shutdown["requested"] = False

    def _completed(
        _self: PumpWatcherService, _irr: Irrigator, _secs: int, started_at: int | None = None
    ) -> dict[str, str]:
        del started_at  # the job passes it by keyword
        return {"outcome": "completed"}

    monkeypatch.setattr(PumpWatcherService, "watch", _completed)
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT)
    env.scheduler.jobs[0]["func"]()
    assert env.adapter.calls == []
    assert _db_state(env) == {"events": [], "activity": []}


def test_deleted_irrigator_ends_the_job_quietly(env: WatchEnv) -> None:
    """Deleted irrigator ends the job quietly."""
    schedule_pump_watcher(env.irrigator_id + 100, 3, STARTED_AT)
    env.scheduler.jobs[0]["func"]()
    assert env.built == []
    assert env.adapter.calls == []


def test_job_failure_is_rolled_back_and_logged(
    env: WatchEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Job failure is rolled back and logged."""

    def _boom(_self: PumpWatcherService, _irr: Irrigator, _secs: int, started_at: int | None = None) -> NoReturn:
        del started_at  # the job passes it by keyword
        msg = "watch exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr(PumpWatcherService, "watch", _boom)
    schedule_pump_watcher(env.irrigator_id, 3, STARTED_AT)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.services.irrigation"):
        env.scheduler.jobs[0]["func"]()  # must not raise
    assert caplog.messages == [f"Pump watcher job failed for irrigator {env.irrigator_id}"]


# ── Watcher result shapes and trip side effects (frozen clock) ────────────────


@dataclass
class TripWorld:
    """What the ``plain`` fixture wires up."""

    repo: IrrigationRepository
    irrigator: Irrigator
    cluster_id: int
    adapter: FakeIrrigatorAdapter
    registry: DeviceRegistry


@pytest.fixture
def plain(frozen_clock: time_machine.Traveller) -> Iterator[TripWorld]:
    """A real watcher world on an in-memory DB: one irrigator and a fake adapter, no scheduler."""
    del frozen_clock  # requested only for its side effect
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repo = IrrigationRepository(session)
    cid = repo.add_cluster("Trip Cluster")
    iid = repo.add_irrigator(
        cluster_id=cid, tuya_device_id="tp", name="Trip Pump", irrigator_type="rainpoint.ik10pw", config={}
    )
    session.commit()
    adapter = FakeIrrigatorAdapter()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda: adapter)
    irrigator = repo.get_irrigator(iid)
    assert irrigator is not None
    yield TripWorld(repo=repo, irrigator=irrigator, cluster_id=cid, adapter=adapter, registry=registry)
    session.close()
    engine.dispose()


def test_interrupted_result_shape_after_one_poll(plain: TripWorld) -> None:
    """Interrupted result shape after one poll."""
    ticks = iter([100.0, 100.0, 100.0, 101.5])  # deadline, warm-up, loop "now", interrupted elapsed
    flag = {"stop": False}

    def _sleep(_s: float) -> None:
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


def test_trip_side_effects_are_exact(plain: TripWorld) -> None:
    """Trip side effects are exact."""
    plain.adapter.set_health(
        DeviceHealthState(
            observed_at=FROZEN_TS,
            alarms=frozenset({HealthAlarm.NO_WATER}),
            raw={"alarm_raw": 1, "source": "local"},
        )
    )
    watcher = PumpWatcherService(plain.repo, plain.registry, warmup_seconds=0, clock=lambda: 0.0, sleep=lambda _s: None)
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
    assert activity.payload_json is not None
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


def test_trip_payload_reprs_unserialisable_alarm_value(plain: TripWorld) -> None:
    """Trip payload reprs unserialisable alarm value."""
    plain.adapter.set_health(
        DeviceHealthState(observed_at=FROZEN_TS, alarms=frozenset({HealthAlarm.NO_WATER}), raw={"alarm_raw": [1, 0]})
    )
    PumpWatcherService(plain.repo, plain.registry, warmup_seconds=0, clock=lambda: 0.0, sleep=lambda _s: None).watch(
        plain.irrigator, 60, started_at=STARTED_AT
    )
    activity = plain.repo.session.query(ActivityEvent).one()
    assert activity.payload_json is not None
    assert json.loads(activity.payload_json)["alarm_raw"] == "[1, 0]"


def test_constructor_clamps_its_knobs(plain: TripWorld) -> None:
    """Constructor clamps its knobs."""
    watcher = PumpWatcherService(plain.repo, plain.registry, poll_seconds=0, warmup_seconds=-3, max_read_failures=0)
    assert (watcher._poll, watcher._warmup, watcher._max_read_failures) == (0.1, 0.0, 1)  # noqa: SLF001 — constructor clamps have no public accessor


def test_trip_side_effect_failures_are_logged_and_the_stop_still_happens(
    plain: TripWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Each trip side effect is isolated (S5): event, activity and monitor failures are logged, never raised.

    Each record is written and committed on its own; a failed one is rolled back and the
    lost alert is requeued on the monitor so it keeps blocking and is raised again.
    """
    plain.adapter.set_health(
        DeviceHealthState(observed_at=FROZEN_TS, alarms=frozenset({HealthAlarm.NO_WATER}), raw={"alarm_raw": 1})
    )

    def _fail(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "db gone"
        raise RuntimeError(msg)

    requeued: list[tuple[object, ...]] = []

    class _BadMonitor:
        def record(self, *_args: object, **_kwargs: object) -> NoReturn:
            msg = "monitor gone"
            raise RuntimeError(msg)

        def mark_unraised(self, *args: object) -> None:
            requeued.append(args)

    monkeypatch.setattr(plain.repo, "add_irrigation_event", _fail)
    monkeypatch.setattr(plain.repo, "add_activity_event", _fail)
    monkeypatch.setattr(plain.repo.session, "commit", _fail)
    watcher = PumpWatcherService(
        plain.repo,
        plain.registry,
        warmup_seconds=0,
        clock=lambda: 0.0,
        sleep=lambda _s: None,
        monitor=cast("DeviceHealthMonitor", _BadMonitor()),  # duck-typed stand-in
    )
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.services.pump_watcher"):
        assert watcher.watch(plain.irrigator, 60, started_at=STARTED_AT)["outcome"] == "tripped"
    iid = plain.irrigator.id
    assert plain.adapter.calls == [("read_health", iid), ("stop", iid)]
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        (
            "CRITICAL",
            (
                f"Pump dry-run detected on irrigator {iid} (cluster {plain.cluster_id}) after 1 polls: "
                "alarm_raw=1, stop_ok=True, stop_msg=fake stop ok"
            ),
        ),
        ("ERROR", f"Failed to log aborted irrigation event for irrigator {iid}"),
        ("ERROR", f"Failed to log the pump dry-run activity for irrigator {iid}"),
        ("ERROR", f"Failed to record dry-run state into health monitor for irrigator {iid}"),
    ]
    assert requeued == [("irrigator", iid, HealthAlarm.NO_WATER)]


def test_trip_after_a_failed_flush_rolls_back_and_still_records_the_rest(
    plain: TripWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """S5 fixed: a real failed flush no longer poisons the session for the later trip records.

    The aborted-event write fails inside the flush (NOT NULL violation), which used to leave the
    session needing a rollback: every later write and the final commit raised
    ``PendingRollbackError`` from inside their own except handlers. Now the failed record is
    rolled back alone; the activity row and the NO_WATER alert are written and committed, and
    nothing escapes the watcher.
    """
    plain.adapter.set_health(
        DeviceHealthState(observed_at=FROZEN_TS, alarms=frozenset({HealthAlarm.NO_WATER}), raw={"alarm_raw": 1})
    )
    session = plain.repo.session

    def _bad_event(**_kwargs: object) -> None:
        session.add(IrrigationEvent(irrigator_id=plain.irrigator.id, timestamp=None, action="aborted"))
        session.flush()

    monkeypatch.setattr(plain.repo, "add_irrigation_event", _bad_event)
    watcher = PumpWatcherService(plain.repo, plain.registry, warmup_seconds=0, clock=lambda: 0.0, sleep=lambda _s: None)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.services.pump_watcher"):
        assert watcher.watch(plain.irrigator, 60, started_at=STARTED_AT)["outcome"] == "tripped"
    iid = plain.irrigator.id
    assert plain.adapter.calls == [("read_health", iid), ("stop", iid)]
    assert [r.getMessage() for r in caplog.records if r.levelname == "ERROR"] == [
        f"Failed to log aborted irrigation event for irrigator {iid}"
    ]
    session.rollback()  # read what was committed, not what is pending
    assert session.query(IrrigationEvent).all() == []
    assert [a.code for a in session.query(ActivityEvent).all()] == ["pump_dry_run"]
    alert = session.query(Alert).one()
    assert (alert.dedup_key, alert.status) == (f"health:irrigator:{iid}:no_water", "open")
