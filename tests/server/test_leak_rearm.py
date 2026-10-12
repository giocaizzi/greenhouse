"""Post-irrigation leak checks survive a restart.

Leak checks are one-shot in-memory scheduler jobs due 30 min after an auto
start, so a restart inside that window used to lose them. On startup the
server now re-arms every auto start in the last ``LEAK_HOLD_HOURS`` whose check
has not completed, and every completed check leaves a ``leak_check`` activity
row so re-arming (and a duplicate job run) is idempotent.
"""

import time
from typing import Never

import pytest
from apscheduler.job import Job
from fastapi import FastAPI
from fastapi.testclient import TestClient

from greenhouse_core.constants import LEAK_CHECK_DELAY_SECONDS, LEAK_HOLD_HOURS
from greenhouse_core.models import (
    ENTITY_CLUSTER,
    EVENT_ACTION_START,
    SOURCE_LEAK,
    TRIGGERED_BY_AUTO,
    TRIGGERED_BY_MANUAL,
    Alert,
    Irrigator,
    TriggeredBy,
)
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.scheduler import scheduler as bg_scheduler
from greenhouse_server.services import irrigation as irrigation_mod
from greenhouse_server.services import irrigation_jobs as irrigation_jobs_mod
from greenhouse_server.services import leak as leak_mod
from greenhouse_server.services.irrigation_jobs import record_leak_check_armed
from server.conftest import _make_stubbed_app


def _seed(app: FastAPI) -> tuple[int, int]:
    """One cluster with one irrigator; returns (cluster_id, irrigator_id)."""
    session = app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        cid = repo.add_cluster("Leak Cluster")
        iid = repo.add_irrigator(
            cluster_id=cid,
            tuya_device_id="fake_tuya_device_aabbccdd",
            name="Pump",
            irrigator_type="rainpoint.ik10pw",
            config={},
        )
        session.commit()
        return cid, iid
    finally:
        session.close()


def _start(app: FastAPI, iid: int, ts: int, triggered_by: TriggeredBy = TRIGGERED_BY_AUTO) -> int:
    session = app.state.session_factory()
    try:
        IrrigationRepository(session).add_irrigation_event(
            irrigator_id=iid, action=EVENT_ACTION_START, triggered_by=triggered_by, duration_minutes=3, timestamp=ts
        )
        session.commit()
        return ts
    finally:
        session.close()


def _irrigator(repo: IrrigationRepository, iid: int) -> Irrigator:
    irrigator = repo.get_irrigator(iid)
    assert irrigator is not None
    return irrigator


def _leak_jobs() -> dict[str, Job]:
    return {j.id: j for j in bg_scheduler.get_jobs() if j.id.startswith("leak-check-")}


@pytest.fixture
def check_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Record LeakDetectionService runs instead of evaluating readings."""
    calls: list[tuple[int, int]] = []

    def _fake(_self: leak_mod.LeakDetectionService, cluster_id: int, started_at: int) -> list[Alert]:
        calls.append((cluster_id, started_at))
        return []

    monkeypatch.setattr(leak_mod.LeakDetectionService, "check_after_irrigation", _fake)
    return calls


class TestRearm:
    """Tests for rearm."""

    @pytest.mark.usefixtures("running_scheduler")
    def test_pending_and_overdue_auto_starts_are_rearmed(self, app: FastAPI) -> None:
        """Pending and overdue auto starts are rearmed."""
        cid, iid = _seed(app)
        now = int(time.time())
        pending = _start(app, iid, now - 600)  # due in 20 min
        overdue = _start(app, iid, now - 2700)  # due 15 min ago, never ran
        _start(app, iid, now - 900, triggered_by=TRIGGERED_BY_MANUAL)  # no armed marker: a hand log, no check
        _start(app, iid, now - LEAK_HOLD_HOURS * 3600 - 60)  # older than the hold horizon

        rearmed = irrigation_jobs_mod.rearm_leak_checks()
        jobs = _leak_jobs()
        assert set(jobs) == {f"leak-check-{cid}-{pending}", f"leak-check-{cid}-{overdue}"}
        assert rearmed == len(jobs)
        due = jobs[f"leak-check-{cid}-{pending}"].next_run_time.timestamp()
        assert due == pytest.approx(pending + LEAK_CHECK_DELAY_SECONDS, abs=1)
        # Overdue checks run right away rather than at their (past) due time.
        assert jobs[f"leak-check-{cid}-{overdue}"].next_run_time.timestamp() == pytest.approx(time.time(), abs=5)

    @pytest.mark.usefixtures("running_scheduler")
    def test_marked_manual_start_is_rearmed_but_a_hand_log_is_not(self, app: FastAPI) -> None:
        """A non-automatic start that armed a check (forced run) leaves a ``leak_check_armed`` marker.

        The re-arm finds it through that marker; a ``start``/``manual`` row without one (a
        logged hand-watering, no pump) gets no check.
        """
        cid, iid = _seed(app)
        now = int(time.time())
        forced = _start(app, iid, now - 600, triggered_by=TRIGGERED_BY_MANUAL)
        _start(app, iid, now - 900, triggered_by=TRIGGERED_BY_MANUAL)  # hand log: no marker
        session = app.state.session_factory()
        try:
            repo = IrrigationRepository(session)
            record_leak_check_armed(repo, _irrigator(repo, iid), forced, triggered_by=TRIGGERED_BY_MANUAL)
            session.commit()
        finally:
            session.close()
        assert irrigation_jobs_mod.rearm_leak_checks() == 1
        assert set(_leak_jobs()) == {f"leak-check-{cid}-{forced}"}

    @pytest.mark.usefixtures("running_scheduler")
    def test_long_manual_start_is_rearmed_with_its_own_window(self, app: FastAPI) -> None:
        """The marker's ``check_after_seconds`` survives a restart: due time and job args keep the window."""
        cid, iid = _seed(app)
        started = _start(app, iid, int(time.time()) - 600, triggered_by=TRIGGERED_BY_MANUAL)
        session = app.state.session_factory()
        try:
            repo = IrrigationRepository(session)
            record_leak_check_armed(
                repo, _irrigator(repo, iid), started, triggered_by=TRIGGERED_BY_MANUAL, after_seconds=5100
            )
            session.commit()
        finally:
            session.close()
        assert irrigation_jobs_mod.rearm_leak_checks() == 1
        job = _leak_jobs()[f"leak-check-{cid}-{started}"]
        assert list(job.args) == [cid, started, 5100]
        assert job.next_run_time.timestamp() == pytest.approx(started + 5100, abs=1)

    @pytest.mark.usefixtures("running_scheduler")
    def test_completed_check_is_not_rearmed(self, app: FastAPI, check_calls: list[tuple[int, int]]) -> None:
        """Completed check is not rearmed."""
        cid, iid = _seed(app)
        overdue = _start(app, iid, int(time.time()) - 2700)
        assert irrigation_jobs_mod.rearm_leak_checks() == 1
        job = _leak_jobs()[f"leak-check-{cid}-{overdue}"]
        job.func(*job.args)
        assert check_calls == [(cid, overdue)]

        bg_scheduler.remove_job(job.id)
        assert irrigation_jobs_mod.rearm_leak_checks() == 0  # marker found
        assert _leak_jobs() == {}

    @pytest.mark.usefixtures("running_scheduler")
    def test_job_runs_check_at_most_once(self, app: FastAPI, check_calls: list[tuple[int, int]]) -> None:
        """Job runs check at most once."""
        cid, iid = _seed(app)
        ts = _start(app, iid, int(time.time()) - 2700)
        irrigation_jobs_mod.rearm_leak_checks()
        job = _leak_jobs()[f"leak-check-{cid}-{ts}"]
        job.func(*job.args)
        job.func(*job.args)  # e.g. normal job + re-armed job both fired
        assert check_calls == [(cid, ts)]

    @pytest.mark.usefixtures("running_scheduler")
    def test_failed_check_leaves_no_marker(self, app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
        """Failed check leaves no marker."""

        def _boom(_self: leak_mod.LeakDetectionService, _cluster_id: int, _started_at: int) -> Never:
            msg = "db hiccup"
            raise RuntimeError(msg)

        monkeypatch.setattr(leak_mod.LeakDetectionService, "check_after_irrigation", _boom)
        cid, iid = _seed(app)
        ts = _start(app, iid, int(time.time()) - 2700)
        irrigation_jobs_mod.rearm_leak_checks()
        job = _leak_jobs()[f"leak-check-{cid}-{ts}"]
        job.func(*job.args)
        bg_scheduler.remove_job(job.id)
        assert irrigation_jobs_mod.rearm_leak_checks() == 1  # still owed

    @pytest.mark.usefixtures("running_scheduler")
    def test_already_held_start_is_not_rechecked(self, app: FastAPI) -> None:
        """A start whose check already raised a hold is treated as checked.

        A start whose check raised a hold before markers existed is treated as checked
        (re-running it could re-raise an alert the operator already resolved).
        """
        cid, iid = _seed(app)
        ts = _start(app, iid, int(time.time()) - 2700)
        session = app.state.session_factory()
        try:
            IrrigationRepository(session).add_activity_event(
                source=SOURCE_LEAK,
                entity_type=ENTITY_CLUSTER,
                entity_id=cid,
                code="leak_hold",
                message="held",
                severity="critical",
                payload={"started_at": ts},
            )
            session.commit()
        finally:
            session.close()
        assert irrigation_jobs_mod.rearm_leak_checks() == 0

    def test_noop_when_scheduler_stopped(self, app: FastAPI) -> None:
        """Noop when scheduler stopped."""
        _, iid = _seed(app)
        _start(app, iid, int(time.time()) - 600)
        assert irrigation_jobs_mod.rearm_leak_checks() == 0


class TestStartupWiring:
    """Tests for startup wiring."""

    def test_startup_rearms_lost_leak_check(self) -> None:
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
    """Tests for auto start timestamp alignment."""

    @pytest.mark.usefixtures("running_scheduler")
    def test_leak_check_keyed_by_the_start_event_timestamp(
        self, app: FastAPI, seeded_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The leak-check job is keyed by the ``start`` row's own timestamp.

        The leak-check job id / started_at must equal the `start` row's timestamp,
        otherwise the restart re-arm (which reads the row) can't match it.
        """
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
