"""Scheduler job registry: dedupe across app builds, truthful `paused`, delete, misfire."""

import logging
import time

from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.executors.base import run_job
from fastapi.testclient import TestClient

from greenhouse_server.scheduler import CHECK_ALL_JOB_ID, get_jobs
from greenhouse_server.scheduler import scheduler as bg_scheduler
from greenhouse_server.services.irrigation import schedule_pump_watcher
from server.conftest import _make_stubbed_app

DEFAULT_JOB_IDS = sorted(
    ["sensor_sync", "check_all", "plant_health_snapshot", "sensor_anomaly", "device_health_monitor"]
)


def _job_ids(client: TestClient) -> list[str]:
    resp = client.get("/api/v1/scheduler/jobs")
    assert resp.status_code == 200
    return sorted(j["id"] for j in resp.json())


def _check_all(client: TestClient) -> dict:
    jobs = client.get("/api/v1/scheduler/jobs").json()
    matches = [j for j in jobs if j["id"] == CHECK_ALL_JOB_ID]
    assert len(matches) == 1, f"expected exactly one check_all, got {len(matches)}"
    return matches[0]


class TestRegistrationIsIdempotent:
    """Building the app more than once in a process must not duplicate jobs."""

    def test_rebuilding_app_does_not_duplicate_jobs(self):
        app1, engine1 = _make_stubbed_app(bypass_auth=True)
        app2, engine2 = _make_stubbed_app(bypass_auth=True)
        try:
            assert _job_ids(TestClient(app2)) == DEFAULT_JOB_IDS
        finally:
            engine1.dispose()
            engine2.dispose()

    def test_persisted_pause_survives_rebuild_then_start(self):
        """A pause re-applied on rebuild must still hold once the scheduler starts.

        Regression: with duplicate pending ``check_all`` jobs, the re-applied
        pause landed on a stale earlier copy while the newest (unpaused) copy
        replaced it at ``start()``, so auto-irrigation silently came back up.
        """
        app1, engine1 = _make_stubbed_app(bypass_auth=True)
        try:
            assert TestClient(app1).post("/api/v1/scheduler/pause").status_code == 200
            # Same DB, fresh app — mirrors a restart within one process.
            from greenhouse_server.app import create_app

            create_app(app1.state.settings, engine=engine1)
            bg_scheduler.start(paused=True)
            try:
                job = bg_scheduler.get_job(CHECK_ALL_JOB_ID)
                assert job is not None
                assert job.next_run_time is None, "persisted pause lost on start"
                assert [j["paused"] for j in get_jobs() if j["id"] == CHECK_ALL_JOB_ID] == [True]
            finally:
                bg_scheduler.shutdown(wait=False)
        finally:
            engine1.dispose()


class TestPausedFlagIsTruthful:
    """`paused` means "explicitly paused", never "scheduler not started"."""

    def test_fresh_app_reports_no_job_paused(self, client):
        jobs = client.get("/api/v1/scheduler/jobs").json()
        assert jobs, "default jobs should be registered"
        assert all(j["paused"] is False for j in jobs), jobs
        # Scheduler is not running → nothing is scheduled to fire.
        assert all(j["next_run_time"] is None for j in jobs), jobs
        assert client.get("/api/v1/preferences").json()["scheduler_paused"] is False

    def test_health_agrees_with_jobs_list(self, client):
        health = client.get("/api/v1/health").json()
        assert health["scheduler_running"] is False
        assert all(j["paused"] is False for j in health["jobs"])

    def test_timezone_change_does_not_pause_check_all(self, client):
        """Regression: `reschedule_for_timezone` read an unstarted job as paused
        and re-paused it, so check_all disagreed with `scheduler_paused=false`."""
        resp = client.put("/api/v1/preferences", json={"timezone": "Europe/Rome"})
        assert resp.status_code == 200
        assert resp.json()["scheduler_paused"] is False
        assert _check_all(client)["paused"] is False

    def test_timezone_change_keeps_real_pause(self, client):
        client.post("/api/v1/scheduler/pause")
        client.put("/api/v1/preferences", json={"timezone": "Europe/Rome"})
        assert _check_all(client)["paused"] is True
        assert client.get("/api/v1/preferences").json()["scheduler_paused"] is True

    def test_pause_resume_on_stopped_scheduler(self, client):
        client.post("/api/v1/scheduler/pause")
        assert _check_all(client)["paused"] is True
        client.post("/api/v1/scheduler/resume")
        check_all = _check_all(client)
        assert check_all["paused"] is False
        assert check_all["next_run_time"] is None  # still not running


class TestRunningScheduler:
    """Semantics once the scheduler is actually started."""

    def test_running_jobs_have_next_run_time_and_are_not_paused(self, client, running_scheduler):
        jobs = client.get("/api/v1/scheduler/jobs").json()
        assert sorted(j["id"] for j in jobs) == DEFAULT_JOB_IDS
        assert all(j["paused"] is False and j["next_run_time"] for j in jobs), jobs

    def test_resume_reschedules_check_all(self, client, running_scheduler):
        assert client.post("/api/v1/scheduler/pause").json() == {"paused": True}
        paused = _check_all(client)
        assert paused["paused"] is True and paused["next_run_time"] is None

        assert client.post("/api/v1/scheduler/resume").json() == {"paused": False}
        resumed = _check_all(client)
        assert resumed["paused"] is False
        assert resumed["next_run_time"] is not None

    def test_periodic_jobs_tolerate_late_wakeups(self, client, running_scheduler):
        """APScheduler's default 1s misfire grace silently drops a late tick."""
        for job_id in DEFAULT_JOB_IDS:
            job = running_scheduler.get_job(job_id)
            assert job.misfire_grace_time is None, job_id
            assert job.coalesce is True, job_id

    def test_pump_watcher_runs_when_scheduled_after_start_time(self, app, client, running_scheduler):
        """Regression: the dry-run watcher's run_date is the (floored) start
        timestamp, already >1s in the past after the Cloud start call, so the
        default 1s misfire grace skipped it and the watcher never ran."""
        app.state.device_registry = app.state.fake_devices.registry
        started_at = int(time.time()) - 2
        assert schedule_pump_watcher(irrigator_id=999, duration_minutes=5, started_at=started_at)
        job = running_scheduler.get_job(f"pump-watcher-999-{started_at}")
        assert job is not None
        events = run_job(job, "default", [job.next_run_time], logging.getLogger(__name__).name)
        codes = [e.code for e in events]
        assert EVENT_JOB_MISSED not in codes
        assert EVENT_JOB_EXECUTED in codes


def _noop() -> None:
    """Stand-in callable for ad-hoc test jobs."""


def _add_adhoc_job(job_id: str = "pump-watcher-1-123") -> str:
    """Register a one-shot job like the per-irrigation watchers do."""
    bg_scheduler.add_job(_noop, "date", run_date="2099-01-01", id=job_id, name="ad-hoc", replace_existing=True)
    return job_id


class TestDeleteJob:
    """DELETE /scheduler/jobs/{job_id}: core jobs are protected, ad-hoc jobs are not."""

    def test_every_startup_job_is_refused_with_409(self, client):
        startup_ids = _job_ids(client)
        assert startup_ids == DEFAULT_JOB_IDS
        for job_id in startup_ids:
            resp = client.delete(f"/api/v1/scheduler/jobs/{job_id}")
            assert resp.status_code == 409, job_id
            assert "/scheduler/pause" in resp.json()["detail"]
        assert _job_ids(client) == DEFAULT_JOB_IDS  # nothing removed

    def test_listing_flags_core_jobs(self, client):
        """`core` lets clients (TUI, MCP) know which jobs DELETE will refuse."""
        jobs = client.get("/api/v1/scheduler/jobs").json()
        assert sorted(j["id"] for j in jobs if j["core"]) == sorted(DEFAULT_JOB_IDS)

    def test_core_job_set_matches_registration(self, client):
        """The protected set is derived from registration, not a hand-kept list."""
        from greenhouse_server.scheduler import core_job_ids

        assert sorted(core_job_ids()) == _job_ids(client)

    def test_core_jobs_refused_on_running_scheduler(self, client, running_scheduler):
        assert client.delete(f"/api/v1/scheduler/jobs/{CHECK_ALL_JOB_ID}").status_code == 409
        assert running_scheduler.get_job(CHECK_ALL_JOB_ID) is not None

    def test_adhoc_job_is_deletable(self, client):
        job_id = _add_adhoc_job()
        assert client.delete(f"/api/v1/scheduler/jobs/{job_id}").json() == {"success": True}
        assert job_id not in _job_ids(client)
        assert client.delete(f"/api/v1/scheduler/jobs/{job_id}").status_code == 404

    def test_adhoc_job_deletable_on_running_scheduler(self, client, running_scheduler):
        job_id = _add_adhoc_job()
        assert client.delete(f"/api/v1/scheduler/jobs/{job_id}").status_code == 200
        assert running_scheduler.get_job(job_id) is None
        assert client.delete(f"/api/v1/scheduler/jobs/{job_id}").status_code == 404

    def test_delete_after_rebuild_removes_job_from_listing(self):
        # Build twice: duplicated pending jobs used to survive a single delete.
        app1, engine1 = _make_stubbed_app(bypass_auth=True)
        app2, engine2 = _make_stubbed_app(bypass_auth=True)
        try:
            c = TestClient(app2)
            job_id = _add_adhoc_job()
            assert c.delete(f"/api/v1/scheduler/jobs/{job_id}").status_code == 200
            assert job_id not in _job_ids(c)
        finally:
            engine1.dispose()
            engine2.dispose()

    def test_delete_unknown_job_404(self, client):
        assert client.delete("/api/v1/scheduler/jobs/nope").status_code == 404
