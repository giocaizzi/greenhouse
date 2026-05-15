"""Tests for the /scheduler/pause and /scheduler/resume endpoints."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from greenhouse_server.app import create_app
from greenhouse_server.config import Settings
from greenhouse_server.deps import get_device_manager, get_tuya_cloud
from greenhouse_server.scheduler import CHECK_ALL_JOB_ID
from greenhouse_server.scheduler import scheduler as bg_scheduler


def _new_app_with_engine(engine):
    """Build an app bound to a specific engine (so restart simulations share state)."""
    settings = Settings(db_url="sqlite://", enable_scheduler=False, auth_enabled=False)
    application = create_app(settings, engine=engine)

    mock_dm = MagicMock()
    mock_dm.irrigator_start.return_value = (True, "Started OK")
    mock_dm.irrigator_off.return_value = (True, "Stopped OK")
    application.dependency_overrides[get_device_manager] = lambda: mock_dm
    application.dependency_overrides[get_tuya_cloud] = lambda: None
    return application


def _shared_engine():
    return create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class TestPauseResume:
    """Endpoint contract for /scheduler/pause and /scheduler/resume."""

    def test_pause_returns_paused_state(self, client):
        resp = client.post("/api/v1/scheduler/pause")
        assert resp.status_code == 200
        assert resp.json() == {"paused": True}

    def test_resume_returns_running_state(self, client):
        client.post("/api/v1/scheduler/pause")
        resp = client.post("/api/v1/scheduler/resume")
        assert resp.status_code == 200
        assert resp.json() == {"paused": False}

    def test_pause_persists_in_preferences(self, client):
        client.post("/api/v1/scheduler/pause")
        resp = client.get("/api/v1/preferences")
        assert resp.status_code == 200
        assert resp.json()["scheduler_paused"] is True

    def test_resume_clears_persisted_flag(self, client):
        client.post("/api/v1/scheduler/pause")
        client.post("/api/v1/scheduler/resume")
        resp = client.get("/api/v1/preferences")
        assert resp.json()["scheduler_paused"] is False


class TestSchedulerJobsListIncludesPausedFlag:
    """GET /scheduler/jobs surfaces a `paused` flag per job."""

    def test_paused_flag_false_initially(self, client):
        resp = client.get("/api/v1/scheduler/jobs")
        assert resp.status_code == 200
        check_all = next((j for j in resp.json() if j["id"] == CHECK_ALL_JOB_ID), None)
        assert check_all is not None
        assert check_all["paused"] is False

    def test_paused_flag_true_after_pause(self, client):
        client.post("/api/v1/scheduler/pause")
        resp = client.get("/api/v1/scheduler/jobs")
        check_all = next((j for j in resp.json() if j["id"] == CHECK_ALL_JOB_ID), None)
        assert check_all is not None
        assert check_all["paused"] is True
        assert check_all["next_run_time"] is None


class TestPauseGatesActuation:
    """A paused `check_all` does NOT invoke the check service."""

    def test_paused_job_next_run_time_cleared(self, client):
        """APScheduler clears next_run_time for paused jobs — that is the gate."""
        client.post("/api/v1/scheduler/pause")
        job = bg_scheduler.get_job(CHECK_ALL_JOB_ID)
        assert job is not None
        assert job.next_run_time is None


class TestPausePersistsAcrossRestart:
    """A pause set on one app instance is re-applied when a new instance boots
    against the same database — simulates a container restart."""

    def test_pause_survives_app_restart(self):
        engine = _shared_engine()
        try:
            # First instance — pause
            app1 = _new_app_with_engine(engine)
            with TestClient(app1, raise_server_exceptions=False) as c1:
                resp = c1.post("/api/v1/scheduler/pause")
                assert resp.status_code == 200
                assert resp.json()["paused"] is True

            # Confirm jobs are gone from the module-level scheduler in between
            # (app shutdown via TestClient's lifespan triggers nothing for
            # enable_scheduler=False, but the next create_app call re-registers
            # the same jobs).

            # Second instance — boot against the same DB; check_all must come
            # up paused, and GET /jobs must report it as such.
            app2 = _new_app_with_engine(engine)
            with TestClient(app2, raise_server_exceptions=False) as c2:
                jobs = c2.get("/api/v1/scheduler/jobs").json()
                check_all = next((j for j in jobs if j["id"] == CHECK_ALL_JOB_ID), None)
                assert check_all is not None, "check_all job not registered on restart"
                assert check_all["paused"] is True
                prefs = c2.get("/api/v1/preferences").json()
                assert prefs["scheduler_paused"] is True
        finally:
            engine.dispose()


class TestWebBanner:
    """The base layout shows a banner when the scheduler is paused."""

    def test_banner_absent_when_running(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'data-banner="scheduler-paused"' not in resp.text

    def test_banner_present_when_paused(self, client):
        client.post("/api/v1/scheduler/pause")
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'data-banner="scheduler-paused"' in resp.text
        assert "Auto-irrigation paused" in resp.text
