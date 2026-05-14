"""Functional tests for the Typer CLI client.

Uses Typer's CliRunner to invoke commands and httpx MockTransport
to simulate server responses without a running server.
"""

import json

import httpx
import pytest
from typer.testing import CliRunner

from greenhouse_cli.main import app

runner = CliRunner()


def _mock_transport(routes: dict):
    """Create an httpx MockTransport from a dict of {(method, path): (status, body)}."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        key = (method, path)

        # Try exact match first, then prefix match for query params
        if key in routes:
            status, body = routes[key]
            if isinstance(body, str):
                return httpx.Response(status, text=body, headers={"content-type": "text/csv"})
            return httpx.Response(status, json=body)

        # Default: 404
        return httpx.Response(404, json={"detail": "Not found"})

    return httpx.MockTransport(handler)


@pytest.fixture
def _patch_client(monkeypatch):
    """Factory fixture: patches IrrigationClient to use mock transport."""

    def _patch(routes: dict):
        transport = _mock_transport(routes)

        original_init = httpx.Client.__init__

        def patched_init(self, *args, **kwargs):
            kwargs.pop("base_url", None)
            kwargs.pop("timeout", None)
            original_init(self, base_url="http://test", transport=transport, timeout=30.0)

        monkeypatch.setattr(httpx.Client, "__init__", patched_init)

    return _patch


class TestClusterCommands:
    def test_cluster_list(self, _patch_client):
        _patch_client(
            {
                ("GET", "/api/v1/clusters"): (200, [{"id": 1, "name": "Test", "environment": "indoor"}]),
            }
        )
        result = runner.invoke(app, ["cluster", "list"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["name"] == "Test"

    def test_cluster_add(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/clusters"): (
                    201,
                    {"id": 1, "name": "New", "location": None, "created_at": 0, "environment": "indoor"},
                ),
            }
        )
        result = runner.invoke(app, ["cluster", "add", "New"])
        assert result.exit_code == 0
        assert "New" in result.stdout


class TestOperationCommands:
    def test_status(self, _patch_client):
        _patch_client(
            {
                ("GET", "/api/v1/clusters/1/status"): (
                    200,
                    {
                        "cluster": {"id": 1, "name": "C1"},
                        "config": None,
                        "plants": [],
                        "sensors": [],
                        "irrigators": [],
                        "decision": None,
                    },
                ),
            }
        )
        result = runner.invoke(app, ["status", "1"])
        assert result.exit_code == 0
        assert "C1" in result.stdout

    def test_irrigate_dry_run(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/clusters/1/irrigate"): (
                    200,
                    {"action": "skip", "reason": "adequate moisture", "confidence": 0.7},
                ),
            }
        )
        result = runner.invoke(app, ["irrigate", "1", "--dry-run"])
        assert result.exit_code == 0
        assert "skip" in result.stdout

    def test_irrigate_with_temp(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/clusters/1/irrigate"): (
                    200,
                    {"action": "irrigate", "reason": "dry", "confidence": 0.9},
                ),
            }
        )
        result = runner.invoke(app, ["irrigate", "1", "--temp", "30.0"])
        assert result.exit_code == 0

    def test_check_all(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/check"): (200, {"results": [], "has_alerts": False}),
            }
        )
        result = runner.invoke(app, ["check", "--all"])
        assert result.exit_code == 0

    def test_check_single(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/clusters/1/check"): (
                    200,
                    {"cluster_id": 1, "cluster_name": "C1", "action": "skipped"},
                ),
            }
        )
        result = runner.invoke(app, ["check", "1"])
        assert result.exit_code == 0

    def test_monitor(self, _patch_client):
        _patch_client(
            {
                ("GET", "/api/v1/clusters/1/monitor"): (
                    200,
                    {"cluster_name": "C1", "sensors": [], "needs_water": []},
                ),
            }
        )
        result = runner.invoke(app, ["monitor", "1"])
        assert result.exit_code == 0

    def test_sync(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/sync"): (200, {"total_synced": 10, "total_new": 3, "total_live": 1, "errors": []}),
            }
        )
        result = runner.invoke(app, ["sync"])
        assert result.exit_code == 0
        assert "3" in result.stdout

    def test_history(self, _patch_client):
        _patch_client(
            {
                ("GET", "/api/v1/clusters/1/history"): (
                    200,
                    {"cluster_name": "C1", "sensors": [], "irrigators": []},
                ),
            }
        )
        result = runner.invoke(app, ["history", "1", "--hours", "48"])
        assert result.exit_code == 0

    def test_health(self, _patch_client):
        _patch_client(
            {
                ("GET", "/api/v1/health"): (200, {"status": "ok", "scheduler_running": True, "jobs": []}),
            }
        )
        result = runner.invoke(app, ["health"])
        assert result.exit_code == 0
        assert "ok" in result.stdout


class TestPlantCommands:
    def test_plant_move_happy_path(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/plants/7/move"): (
                    200,
                    {"id": 7, "cluster_id": 3, "species": "Fern"},
                ),
            }
        )
        result = runner.invoke(app, ["plant", "move", "7", "--to-cluster", "3"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["id"] == 7
        assert data["cluster_id"] == 3

    def test_plant_move_missing_plant(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/plants/999/move"): (404, {"detail": "Plant not found"}),
            }
        )
        result = runner.invoke(app, ["plant", "move", "999", "--to-cluster", "3"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_plant_move_same_cluster(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/plants/7/move"): (
                    400,
                    {"detail": "Plant 7 already belongs to cluster 3"},
                ),
            }
        )
        result = runner.invoke(app, ["plant", "move", "7", "--to-cluster", "3"])
        assert result.exit_code == 1
        assert "already" in result.output.lower()


class TestErrorHandling:
    def test_server_404(self, _patch_client):
        _patch_client(
            {
                ("GET", "/api/v1/clusters/999/status"): (404, {"detail": "Cluster not found"}),
            }
        )
        result = runner.invoke(app, ["status", "999"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_server_unreachable(self, monkeypatch):
        """CLI errors clearly when server is unreachable."""
        # Don't mock transport — let it fail to connect
        monkeypatch.setenv("IRRIGATION_SERVER_URL", "http://127.0.0.1:1")
        result = runner.invoke(app, ["health"])
        assert result.exit_code == 1
        assert "connect" in result.output.lower() or "error" in result.output.lower()

    def test_check_with_alerts_exits_2(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/check"): (200, {"results": [{"alerts": ["low"]}], "has_alerts": True}),
            }
        )
        result = runner.invoke(app, ["check", "--all"])
        assert result.exit_code == 2

    def test_monitor_needs_water_exits_2(self, _patch_client):
        _patch_client(
            {
                ("GET", "/api/v1/clusters/1/monitor"): (
                    200,
                    {"cluster_name": "C1", "sensors": [], "needs_water": ["Sensor A: 30%"]},
                ),
            }
        )
        result = runner.invoke(app, ["monitor", "1"])
        assert result.exit_code == 2


class TestServerFlag:
    def test_custom_server_url(self, _patch_client):
        _patch_client(
            {
                ("GET", "/api/v1/clusters"): (200, []),
            }
        )
        result = runner.invoke(app, ["--server", "http://192.168.1.50:8000", "cluster", "list"])
        assert result.exit_code == 0


class TestSchedulerCommands:
    def test_pause(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/scheduler/pause"): (200, {"paused": True}),
            }
        )
        result = runner.invoke(app, ["scheduler", "pause"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"paused": True}

    def test_resume(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/scheduler/resume"): (200, {"paused": False}),
            }
        )
        result = runner.invoke(app, ["scheduler", "resume"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"paused": False}

    def test_status_lists_jobs(self, _patch_client):
        _patch_client(
            {
                ("GET", "/api/v1/scheduler/jobs"): (
                    200,
                    [
                        {
                            "id": "check_all",
                            "name": "Check all clusters",
                            "trigger": "interval[6:00:00]",
                            "next_run_time": None,
                            "paused": True,
                        }
                    ],
                ),
            }
        )
        result = runner.invoke(app, ["scheduler", "status"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data[0]["id"] == "check_all"
        assert data[0]["paused"] is True

    def test_pause_server_error_exits_1(self, _patch_client):
        _patch_client(
            {
                ("POST", "/api/v1/scheduler/pause"): (404, {"detail": "Job check_all not found"}),
            }
        )
        result = runner.invoke(app, ["scheduler", "pause"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()
