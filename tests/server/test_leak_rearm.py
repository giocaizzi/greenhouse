"""Post-irrigation leak checks survive a restart.

Leak checks are one-shot in-memory scheduler jobs due 30 min after an auto
start, so a restart inside that window used to lose them. On startup the
server now re-arms every auto start in the last ``LEAK_HOLD_HOURS`` whose check
has not completed, and every completed check leaves a ``leak_check`` activity
row so re-arming (and a duplicate job run) is idempotent.
"""

import time

import pytest
from fastapi.testclient import TestClient

from greenhouse_core.constants import LEAK_CHECK_DELAY_SECONDS, LEAK_HOLD_HOURS
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.scheduler import scheduler as bg_scheduler
from greenhouse_server.services import irrigation as irrigation_mod
from server.conftest import _make_stubbed_app


def _seed(app) -> tuple[int, int]:
    """One cluster with one irrigator; returns (cluster_id, irrigator_id)."""
    session = app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        cid = repo.add_cluster("Leak Cluster")
        iid = repo.add_irrigator(
            cluster_id=cid,
            tuya_device_id="fake_tuya_device_aabbccdd",
            name="Pump",
            irrigator_type="tuya_cloud",
            config={},
        )
        session.commit()
        return cid, iid
    finally:
        session.close()


def _start(app, iid: int, ts: int, triggered_by: str = "auto") -> int:
    session = app.state.session_factory()
    try:
        IrrigationRepository(session).add_irrigation_event(
            irrigator_id=iid, action="start", triggered_by=triggered_by, duration_minutes=3, timestamp=ts
        )
        session.commit()
        return ts
    finally:
        session.close()


def _leak_jobs() -> dict[str, object]:
    return {j.id: j for j in bg_scheduler.get_jobs() if j.id.startswith("leak-check-")}


@pytest.fixture
def check_calls(monkeypatch):
    """Record LeakDetectionService runs instead of evaluating readings."""
    from greenhouse_server.services import leak as leak_mod

    calls: list[tuple[int, int]] = []

    def _fake(self, cluster_id, started_at):
        calls.append((cluster_id, started_at))
        return []

    monkeypatch.setattr(leak_mod.LeakDetectionService, "check_after_irrigation", _fake)
    return calls


class TestRearm:
    def test_pending_and_overdue_auto_starts_are_rearmed(self, app, running_scheduler):
        cid, iid = _seed(app)
        now = int(time.time())
        pending = _start(app, iid, now - 600)  # due in 20 min
        overdue = _start(app, iid, now - 2700)  # due 15 min ago, never ran
        _start(app, iid, now - 900, triggered_by="manual")  # manual starts never get a leak check
        _start(app, iid, now - LEAK_HOLD_HOURS * 3600 - 60)  # older than the hold horizon

        assert irrigation_mod.rearm_leak_checks() == 2
        jobs = _leak_jobs()
        assert set(jobs) == {f"leak-check-{cid}-{pending}", f"leak-check-{cid}-{overdue}"}
        due = jobs[f"leak-check-{cid}-{pending}"].next_run_time.timestamp()
        assert due == pytest.approx(pending + LEAK_CHECK_DELAY_SECONDS, abs=1)
        # Overdue checks run right away rather than at their (past) due time.
        assert jobs[f"leak-check-{cid}-{overdue}"].next_run_time.timestamp() == pytest.approx(time.time(), abs=5)

    def test_completed_check_is_not_rearmed(self, app, running_scheduler, check_calls):
        cid, iid = _seed(app)
        overdue = _start(app, iid, int(time.time()) - 2700)
        assert irrigation_mod.rearm_leak_checks() == 1
        job = _leak_jobs()[f"leak-check-{cid}-{overdue}"]
        job.func(*job.args)
        assert check_calls == [(cid, overdue)]

        bg_scheduler.remove_job(job.id)
        assert irrigation_mod.rearm_leak_checks() == 0  # marker found
        assert _leak_jobs() == {}

    def test_job_runs_check_at_most_once(self, app, running_scheduler, check_calls):
        cid, iid = _seed(app)
        ts = _start(app, iid, int(time.time()) - 2700)
        irrigation_mod.rearm_leak_checks()
        job = _leak_jobs()[f"leak-check-{cid}-{ts}"]
        job.func(*job.args)
        job.func(*job.args)  # e.g. normal job + re-armed job both fired
        assert check_calls == [(cid, ts)]

    def test_failed_check_leaves_no_marker(self, app, running_scheduler, monkeypatch):
        from greenhouse_server.services import leak as leak_mod

        def _boom(self, cluster_id, started_at):
            raise RuntimeError("db hiccup")

        monkeypatch.setattr(leak_mod.LeakDetectionService, "check_after_irrigation", _boom)
        cid, iid = _seed(app)
        ts = _start(app, iid, int(time.time()) - 2700)
        irrigation_mod.rearm_leak_checks()
        job = _leak_jobs()[f"leak-check-{cid}-{ts}"]
        job.func(*job.args)
        bg_scheduler.remove_job(job.id)
        assert irrigation_mod.rearm_leak_checks() == 1  # still owed

    def test_already_held_start_is_not_rechecked(self, app, running_scheduler):
        """A start whose check raised a hold before markers existed is treated as checked
        (re-running it could re-raise an alert the operator already resolved)."""
        cid, iid = _seed(app)
        ts = _start(app, iid, int(time.time()) - 2700)
        session = app.state.session_factory()
        try:
            IrrigationRepository(session).add_activity_event(
                source="leak",
                entity_type="cluster",
                entity_id=cid,
                code="leak_hold",
                message="held",
                severity="critical",
                payload={"started_at": ts},
            )
            session.commit()
        finally:
            session.close()
        assert irrigation_mod.rearm_leak_checks() == 0

    def test_noop_when_scheduler_stopped(self, app):
        _, iid = _seed(app)
        _start(app, iid, int(time.time()) - 600)
        assert irrigation_mod.rearm_leak_checks() == 0


class TestStartupWiring:
    def test_startup_rearms_lost_leak_check(self):
        """Regression: a restart within the 30-min window silently lost the check."""
        application, engine = _make_stubbed_app(bypass_auth=True, enable_scheduler=True)
        try:
            cid, iid = _seed(application)
            ts = _start(application, iid, int(time.time()) - 600)
            with TestClient(application):
                assert f"leak-check-{cid}-{ts}" in _leak_jobs()
        finally:
            engine.dispose()


class TestAutoStartTimestampAlignment:
    def test_leak_check_keyed_by_the_start_event_timestamp(self, app, seeded_client, running_scheduler, monkeypatch):
        """The leak-check job id / started_at must equal the `start` row's timestamp,
        otherwise the restart re-arm (which reads the row) can't match it."""
        session = app.state.session_factory()
        repo = IrrigationRepository(session)
        now = int(time.time())
        for i in range(4):
            repo.add_sensor_reading(sensor_id=1, timestamp=now - 600 * i, soil_moisture=10.0, temperature=22.0)
        session.commit()
        session.close()

        # The service's clock runs a second ahead of the repository's, as when
        # the start timestamp is taken after a slow commit in production.
        class _AheadClock:
            @staticmethod
            def time() -> float:
                return time.time() + 1

        monkeypatch.setattr(irrigation_mod, "_time", _AheadClock)

        resp = seeded_client.post(
            "/api/v1/clusters/1/irrigate",
            json={"dry_run": False, "no_sync": True, "force": True, "temp_override": 22.0},
        )
        assert resp.json()["action"] == "irrigated"

        session = app.state.session_factory()
        try:
            start = next(e for e in IrrigationRepository(session).get_recent_events(1) if e.action == "start")
        finally:
            session.close()
        assert f"leak-check-1-{start.timestamp}" in _leak_jobs()
