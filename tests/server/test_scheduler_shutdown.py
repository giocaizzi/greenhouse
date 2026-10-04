"""Server shutdown during an irrigation: prompt exit, pump-watcher safety kept.

The dry-run watcher is a scheduler job that polls DP 105 for the whole
irrigation. APScheduler's worker threads are non-daemon, so before the fix the
interpreter waited for the watcher to run out its full duration on shutdown.
Now shutdown interrupts the watcher, which hands off to a shutdown policy:
auto-started cycles are stopped (the pump is not left running unwatched),
manually started ones keep running on the device's own timer with a warning.
"""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fake_devices import FakeIrrigatorAdapter
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.models import Base
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.irrigation import handle_watcher_interrupted
from greenhouse_server.services.pump_watcher import PumpWatcherService

REPO_ROOT = Path(__file__).resolve().parents[2]

_SCRIPT = textwrap.dedent(
    """
    import atexit, sys, time
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    from fake_devices import FakeIrrigatorAdapter
    from greenhouse_core.devices import DeviceRegistry
    from greenhouse_core.repository import IrrigationRepository
    from greenhouse_server.app import create_app
    from greenhouse_server.config import Settings
    from greenhouse_server.scheduler import scheduler
    from greenhouse_server.services.irrigation import schedule_pump_watcher

    triggered_by = sys.argv[1]
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    settings = Settings(
        _env_file=None, db_url="sqlite://", enable_scheduler=True, auth_enabled=False,
        pump_watcher_poll_seconds=0.2, pump_watcher_warmup_seconds=0,
    )
    app = create_app(settings, engine=engine)
    scheduler.pause_job("check_all")  # keep the hourly tick out of the way

    fake = FakeIrrigatorAdapter()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda a=fake: a)
    app.state.device_registry = registry

    session = app.state.session_factory()
    repo = IrrigationRepository(session)
    cid = repo.add_cluster("C")
    iid = repo.add_irrigator(cluster_id=cid, tuya_device_id="fake_tuya_device_aabbccdd",
                             name="Pump", irrigator_type="rainpoint.ik10pw", config={})
    session.commit()
    session.close()

    # Runs after the interpreter has joined the scheduler's worker threads.
    atexit.register(lambda: print("STOPS", sum(1 for c in fake.calls if c[0] == "stop"), flush=True))

    with TestClient(app):
        assert schedule_pump_watcher(iid, 1, int(time.time()), triggered_by=triggered_by)
        deadline = time.time() + 15
        while not any(c[0] == "read_health" for c in fake.calls):
            if time.time() > deadline:
                sys.exit("watcher never started")
            time.sleep(0.05)
    print("LIFESPAN_DONE", flush=True)
    """
)


def _run_server_shutdown(tmp_path: Path, triggered_by: str) -> tuple[float, str]:
    script = tmp_path / "shutdown_during_irrigation.py"
    script.write_text(_SCRIPT)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        str(REPO_ROOT / p) for p in ("libs/greenhouse-core", "libs/greenhouse-server", "libs/greenhouse-cli", "tests")
    )
    t0 = time.monotonic()
    # A 1-minute irrigation: the pre-fix process exits only after ~60s.
    proc = subprocess.run(
        [sys.executable, str(script), triggered_by],
        capture_output=True,
        text=True,
        timeout=45,
        env=env,
        cwd=tmp_path,
    )
    elapsed = time.monotonic() - t0
    assert proc.returncode == 0, proc.stderr[-3000:]
    return elapsed, proc.stdout


@pytest.mark.parametrize(("triggered_by", "stops"), [("auto", 1), ("manual", 0)])
def test_shutdown_during_irrigation_is_prompt(tmp_path, triggered_by, stops):
    elapsed, out = _run_server_shutdown(tmp_path, triggered_by)
    assert "LIFESPAN_DONE" in out
    assert f"STOPS {stops}" in out, out
    assert elapsed < 30, f"shutdown blocked for {elapsed:.1f}s"


# ── In-process units ────────────────────────────────────────────────────────


@pytest.fixture
def repo():
    engine = create_engine("sqlite://", echo=False)
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield IrrigationRepository(session)
    session.close()
    engine.dispose()


@pytest.fixture
def irrigator(repo):
    cluster_id = repo.add_cluster("Test Cluster")
    irrigator_id = repo.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id="fake_irrigator_pump",
        name="Pump Irrigator",
        irrigator_type="rainpoint.ik10pw",
        config={},
    )
    repo.session.commit()
    return repo.get_irrigator(irrigator_id)


def _registry(adapter: FakeIrrigatorAdapter) -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda a=adapter: a)
    return registry


class TestWatcherInterruption:
    def test_stop_requested_ends_watch_immediately(self, repo, irrigator):
        adapter = FakeIrrigatorAdapter()
        flag = {"stop": False}

        def _sleep(_seconds: float) -> None:
            flag["stop"] = True  # shutdown arrives while the watcher sleeps

        watcher = PumpWatcherService(repo, _registry(adapter), sleep=_sleep, stop_requested=lambda: flag["stop"])
        result = watcher.watch(irrigator, duration_seconds=3600)
        assert result["outcome"] == "interrupted"
        assert result["polls"] == 1
        # The watcher itself never actuates on interruption — that's the policy's job.
        assert not [c for c in adapter.calls if c[0] == "stop"]

    def test_no_stop_requested_runs_to_completion(self, repo, irrigator):
        ticks = iter(range(10_000))
        watcher = PumpWatcherService(
            repo,
            _registry(FakeIrrigatorAdapter()),
            clock=lambda: float(next(ticks)),
            sleep=lambda _s: None,
            stop_requested=lambda: False,
        )
        assert watcher.watch(irrigator, duration_seconds=5)["outcome"] == "completed"


class TestShutdownPolicy:
    def test_auto_cycle_is_stopped_and_recorded(self, repo, irrigator):
        adapter = FakeIrrigatorAdapter()
        stopped = handle_watcher_interrupted(
            repo, _registry(adapter), irrigator, triggered_by="auto", started_at=int(time.time())
        )
        repo.session.commit()
        assert stopped is True
        assert [c for c in adapter.calls if c[0] == "stop"] == [("stop", irrigator.id)]
        events = repo.get_recent_events(irrigator.id)
        assert [(e.action, e.triggered_by) for e in events] == [("stop", "shutdown")]
        activity = repo.list_activity_events()
        assert any(a.code == "pump_watcher_shutdown" for a in activity)

    def test_manual_cycle_is_left_running_with_warning(self, repo, irrigator, caplog):
        adapter = FakeIrrigatorAdapter()
        stopped = handle_watcher_interrupted(
            repo, _registry(adapter), irrigator, triggered_by="manual", started_at=int(time.time())
        )
        repo.session.commit()
        assert stopped is False
        assert not [c for c in adapter.calls if c[0] == "stop"]
        assert repo.get_recent_events(irrigator.id) == []
        assert any(a.code == "pump_watcher_shutdown" for a in repo.list_activity_events())
        assert "unprotected" in caplog.text

    def test_auto_stop_failure_is_logged_not_raised(self, repo, irrigator, caplog):
        adapter = FakeIrrigatorAdapter(stop_result=(False, "device unreachable"))
        stopped = handle_watcher_interrupted(
            repo, _registry(adapter), irrigator, triggered_by="auto", started_at=int(time.time())
        )
        assert stopped is False
        assert "device unreachable" in caplog.text
