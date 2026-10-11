"""Functional tests for the Typer CLI client.

Uses Typer's CliRunner to invoke commands and httpx MockTransport
to simulate server responses without a running server.
"""

import json
from http import HTTPStatus

import httpx
import pytest
from typer.testing import CliRunner

from greenhouse_cli.main import app

EXIT_NEEDS_ATTENTION = 2  # ``check --all`` with alerts, ``monitor`` with plants needing water
STUB_PLANT_ID, STUB_TARGET_CLUSTER_ID = 7, 3
STUB_IRRIGATOR_ID = 7
STUB_GLOBAL_QUIET_END_HOUR = 5
ADD_RESERVOIR_L, ADD_FLOW_L_PER_MIN = 20, 1.5
UPDATE_RESERVOIR_L, UPDATE_FLOW_L_PER_MIN = 15.5, 2

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

        # Unknown route: answer like the server does for a missing resource.
        return httpx.Response(404, json={"detail": "Not found"})

    return httpx.MockTransport(handler)


@pytest.fixture
def patch_client(monkeypatch):
    """Factory fixture: patches IrrigationClient to use mock transport."""

    def _patch(routes: dict):
        transport = _mock_transport(routes)

        original_init = httpx.Client.__init__

        def patched_init(self, *_args, **kwargs):
            kwargs.pop("base_url", None)
            kwargs.pop("timeout", None)
            original_init(self, base_url="http://test", transport=transport, timeout=30.0)

        monkeypatch.setattr(httpx.Client, "__init__", patched_init)

    return _patch


class TestClusterCommands:
    """``greenhouse cluster`` sub-app requests and output."""

    def test_cluster_list(self, patch_client):
        """``cluster list`` prints the server's cluster list as JSON."""
        patch_client(
            {
                ("GET", "/api/v1/clusters"): (200, [{"id": 1, "name": "Test", "environment": "indoor"}]),
            }
        )
        result = runner.invoke(app, ["cluster", "list"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["name"] == "Test"

    def test_cluster_add(self, patch_client):
        """``cluster add`` posts the new cluster and prints the created row."""
        patch_client(
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
    """Top-level operation commands (status, irrigate, check, monitor, sync, history, health)."""

    def test_status(self, patch_client):
        """``status`` prints the cluster status payload."""
        patch_client(
            {
                ("GET", "/api/v1/clusters/1/status"): (
                    200,
                    {
                        "cluster": {"id": 1, "name": "C1"},
                        "config": None,
                        "plants": [],
                        "sensors": [],
                        "irrigator": None,
                        "decision": None,
                    },
                ),
            }
        )
        result = runner.invoke(app, ["status", "1"])
        assert result.exit_code == 0
        assert "C1" in result.stdout

    def test_irrigate_dry_run(self, patch_client):
        """``irrigate --dry-run`` prints the engine's decision (here a skip)."""
        patch_client(
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

    def test_irrigate_with_temp(self, patch_client):
        """``irrigate --temp`` is accepted and the command succeeds."""
        patch_client(
            {
                ("POST", "/api/v1/clusters/1/irrigate"): (
                    200,
                    {"action": "irrigate", "reason": "dry", "confidence": 0.9},
                ),
            }
        )
        result = runner.invoke(app, ["irrigate", "1", "--temp", "30.0"])
        assert result.exit_code == 0

    def test_check_all(self, patch_client):
        """``check --all`` posts to the global check endpoint and exits 0 without alerts."""
        patch_client(
            {
                ("POST", "/api/v1/check"): (200, {"results": [], "has_alerts": False}),
            }
        )
        result = runner.invoke(app, ["check", "--all"])
        assert result.exit_code == 0

    def test_check_single(self, patch_client):
        """``check`` with a cluster id posts to that cluster's check endpoint."""
        patch_client(
            {
                ("POST", "/api/v1/clusters/1/check"): (
                    200,
                    {"cluster_id": 1, "cluster_name": "C1", "action": "skipped"},
                ),
            }
        )
        result = runner.invoke(app, ["check", "1"])
        assert result.exit_code == 0

    def test_monitor(self, patch_client):
        """``monitor`` reads the cluster monitor endpoint."""
        patch_client(
            {
                ("GET", "/api/v1/clusters/1/monitor"): (
                    200,
                    {"cluster_name": "C1", "sensors": [], "needs_water": []},
                ),
            }
        )
        result = runner.invoke(app, ["monitor", "1"])
        assert result.exit_code == 0

    def test_sync(self, patch_client):
        """``sync`` posts the sensor sync and prints its counters."""
        patch_client(
            {
                ("POST", "/api/v1/sync"): (200, {"total_synced": 10, "total_new": 3, "total_live": 1, "errors": []}),
            }
        )
        result = runner.invoke(app, ["sync"])
        assert result.exit_code == 0
        assert "3" in result.stdout

    def test_history(self, patch_client):
        """``history`` reads the cluster's irrigation history."""
        patch_client(
            {
                ("GET", "/api/v1/clusters/1/history"): (
                    200,
                    {"cluster_name": "C1", "sensors": [], "irrigator": None},
                ),
            }
        )
        result = runner.invoke(app, ["history", "1", "--hours", "48"])
        assert result.exit_code == 0

    def test_health(self, patch_client):
        """``health`` prints the server health payload."""
        patch_client(
            {
                ("GET", "/api/v1/health"): (200, {"status": "ok", "scheduler_running": True, "jobs": []}),
            }
        )
        result = runner.invoke(app, ["health"])
        assert result.exit_code == 0
        assert "ok" in result.stdout


class TestPlantCommands:
    """``greenhouse plant move`` outcomes."""

    def test_plant_move_happy_path(self, patch_client):
        """Moving a plant posts the target cluster and prints the moved plant."""
        patch_client(
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
        assert data["id"] == STUB_PLANT_ID
        assert data["cluster_id"] == STUB_TARGET_CLUSTER_ID

    def test_plant_move_missing_plant(self, patch_client):
        """A 404 for an unknown plant exits 1 with the server's message."""
        patch_client(
            {
                ("POST", "/api/v1/plants/999/move"): (404, {"detail": "Plant not found"}),
            }
        )
        result = runner.invoke(app, ["plant", "move", "999", "--to-cluster", "3"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_plant_move_same_cluster(self, patch_client):
        """A 400 for a move into the same cluster exits 1 with the server's message."""
        patch_client(
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
    """Exit codes for server errors and alerting results."""

    def test_server_404(self, patch_client):
        """A 404 from the server exits 1 and reports the detail on stderr."""
        patch_client(
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

    def test_check_with_alerts_exits_2(self, patch_client):
        """``check --all`` exits 2 when the server reports alerts, so scripts can react."""
        patch_client(
            {
                ("POST", "/api/v1/check"): (200, {"results": [{"alerts": ["low"]}], "has_alerts": True}),
            }
        )
        result = runner.invoke(app, ["check", "--all"])
        assert result.exit_code == EXIT_NEEDS_ATTENTION

    def test_monitor_needs_water_exits_2(self, patch_client):
        """``monitor`` exits 2 when a plant needs water."""
        patch_client(
            {
                ("GET", "/api/v1/clusters/1/monitor"): (
                    200,
                    {"cluster_name": "C1", "sensors": [], "needs_water": ["Sensor A: 30%"]},
                ),
            }
        )
        result = runner.invoke(app, ["monitor", "1"])
        assert result.exit_code == EXIT_NEEDS_ATTENTION


class TestClientLifecycle:
    """The HTTP client is always closed after a command."""

    @pytest.mark.parametrize(("status", "exit_code"), [(200, 0), (404, 1)])
    def test_call_closes_the_client_it_opened(self, monkeypatch, status, exit_code):
        """Every CLI call closes its HTTP client, on success and on a server error."""
        body = {"status": "ok"} if status == HTTPStatus.OK else {"detail": "Not found"}
        transport = httpx.MockTransport(lambda _request: httpx.Response(status, json=body))
        clients: list[httpx.Client] = []
        original_init = httpx.Client.__init__

        def recording_init(self, *args, **kwargs):
            original_init(self, *args, **{**kwargs, "transport": transport})
            clients.append(self)

        monkeypatch.setattr(httpx.Client, "__init__", recording_init)
        result = runner.invoke(app, ["health"])
        assert result.exit_code == exit_code
        assert len(clients) == 1
        assert clients[0].is_closed


class TestServerFlag:
    """The root ``--server`` option."""

    def test_custom_server_url(self, patch_client):
        """``--server`` overrides the default server URL."""
        patch_client(
            {
                ("GET", "/api/v1/clusters"): (200, []),
            }
        )
        result = runner.invoke(app, ["--server", "http://192.168.1.50:8000", "cluster", "list"])
        assert result.exit_code == 0


class TestSchedulerCommands:
    """``greenhouse scheduler`` sub-app."""

    def test_pause(self, patch_client):
        """``scheduler pause`` posts the pause request."""
        patch_client(
            {
                ("POST", "/api/v1/scheduler/pause"): (200, {"paused": True}),
            }
        )
        result = runner.invoke(app, ["scheduler", "pause"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"paused": True}

    def test_resume(self, patch_client):
        """``scheduler resume`` posts the resume request."""
        patch_client(
            {
                ("POST", "/api/v1/scheduler/resume"): (200, {"paused": False}),
            }
        )
        result = runner.invoke(app, ["scheduler", "resume"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"paused": False}

    def test_status_lists_jobs(self, patch_client):
        """``scheduler status`` lists the scheduler's jobs."""
        patch_client(
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

    def test_pause_server_error_exits_1(self, patch_client):
        """A server error on pause exits 1 with the server's message."""
        patch_client(
            {
                ("POST", "/api/v1/scheduler/pause"): (404, {"detail": "Job check_all not found"}),
            }
        )
        result = runner.invoke(app, ["scheduler", "pause"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


@pytest.fixture
def patch_capture(monkeypatch):
    """Patch IrrigationClient and capture each request's (method, path, json body).

    Returns the list the handler appends to so a test can assert exactly which
    fields the CLI forwarded to the server.
    """
    captured: list[dict] = []

    def _patch(routes: dict):
        def handler(request: httpx.Request) -> httpx.Response:
            body = None
            if request.content:
                body = json.loads(request.content)
            captured.append({"method": request.method, "path": request.url.path, "json": body})
            status, payload = routes.get((request.method, request.url.path), (404, {"detail": "Not found"}))
            return httpx.Response(status, json=payload)

        transport = httpx.MockTransport(handler)
        original_init = httpx.Client.__init__

        def patched_init(self, *_args, **kwargs):
            kwargs.pop("base_url", None)
            kwargs.pop("timeout", None)
            original_init(self, base_url="http://test", transport=transport, timeout=30.0)

        monkeypatch.setattr(httpx.Client, "__init__", patched_init)
        return captured

    return _patch


class TestConfigCommands:
    """``greenhouse config`` and ``config global`` sub-apps."""

    def test_set_forwards_quiet_hours_and_caps(self, patch_capture):
        """``config set`` forwards quiet hours and the daily cap under their API field names."""
        captured = patch_capture({("PUT", "/api/v1/clusters/1/config"): (200, {"id": 1, "cluster_id": 1})})
        result = runner.invoke(
            app,
            ["config", "set", "--cluster", "1", "--quiet-start", "0", "--quiet-end", "5", "--daily-cap", "30"],
        )
        assert result.exit_code == 0
        assert captured[0]["json"] == {"quiet_start_hour": 0, "quiet_end_hour": 5, "daily_cap_minutes": 30}

    def test_set_omits_unset_fields(self, patch_capture):
        """Options left unset are not sent, so the server keeps their current values."""
        captured = patch_capture({("PUT", "/api/v1/clusters/1/config"): (200, {"id": 1, "cluster_id": 1})})
        result = runner.invoke(app, ["config", "set", "--cluster", "1", "--mode", "smart"])
        assert result.exit_code == 0
        # null-valued options are dropped so the server leaves them unchanged
        assert captured[0]["json"] == {"mode": "smart"}

    def test_effective_reads_merged_config(self, patch_client):
        """``config effective`` reads the merged (cluster over global) config."""
        patch_client(
            {
                ("GET", "/api/v1/clusters/1/config/effective"): (
                    200,
                    {
                        "cluster_id": 1,
                        "declared": None,
                        "effective": {"quiet_start_hour": {"value": 0, "source": "global"}},
                    },
                ),
            }
        )
        result = runner.invoke(app, ["config", "effective", "--cluster", "1"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["effective"]["quiet_start_hour"]["source"] == "global"

    def test_global_get(self, patch_client):
        """``config global get`` prints the global config."""
        patch_client({("GET", "/api/v1/config/global"): (200, {"id": 1, "quiet_start_hour": 0, "quiet_end_hour": 5})})
        result = runner.invoke(app, ["config", "global", "get"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["quiet_end_hour"] == STUB_GLOBAL_QUIET_END_HOUR

    def test_global_set_forwards_only_given_fields(self, patch_capture):
        """``config global set`` sends only the fields given on the command line."""
        captured = patch_capture({("PUT", "/api/v1/config/global"): (200, {"id": 1})})
        result = runner.invoke(app, ["config", "global", "set", "--quiet-start", "1", "--quiet-end", "6"])
        assert result.exit_code == 0
        assert captured[0]["json"] == {"quiet_start_hour": 1, "quiet_end_hour": 6}


class TestIrrigateForce:
    """The ``irrigate --force`` flag."""

    def test_force_flag_forwarded(self, patch_capture):
        """``--force`` is forwarded as ``force: true``."""
        captured = patch_capture({("POST", "/api/v1/clusters/1/irrigate"): (200, {"action": "irrigate"})})
        result = runner.invoke(app, ["irrigate", "1", "--force"])
        assert result.exit_code == 0
        assert captured[0]["json"]["force"] is True

    def test_force_defaults_false(self, patch_capture):
        """Without ``--force`` the request carries ``force: false``."""
        captured = patch_capture({("POST", "/api/v1/clusters/1/irrigate"): (200, {"action": "skip"})})
        result = runner.invoke(app, ["irrigate", "1"])
        assert result.exit_code == 0
        assert captured[0]["json"]["force"] is False


class TestIrrigatorCapacity:
    """Irrigator add/update forward the reservoir + flow-rate capacity fields."""

    def test_add_forwards_capacity_fields(self, patch_capture):
        """``irrigator add`` forwards the reservoir and flow-rate options."""
        captured = patch_capture({("POST", "/api/v1/clusters/1/irrigator"): (201, {"id": 7})})
        result = runner.invoke(
            app,
            [
                "irrigator",
                "add",
                "--cluster",
                "1",
                "--device-id",
                "fake_tuya_device_aabbccdd",
                "--name",
                "Tank pump",
                "--type",
                "rainpoint.ik10pw",
                "--reservoir-l",
                "20",
                "--flow-rate-l-per-min",
                "1.5",
            ],
        )
        assert result.exit_code == 0
        body = captured[0]["json"]
        assert body["reservoir_l"] == ADD_RESERVOIR_L
        assert body["flow_rate_l_per_min"] == ADD_FLOW_L_PER_MIN

    def test_add_omits_capacity_when_unset(self, patch_capture):
        """``irrigator add`` leaves the capacity fields out when they are not given."""
        captured = patch_capture({("POST", "/api/v1/clusters/1/irrigator"): (201, {"id": 7})})
        result = runner.invoke(
            app,
            [
                "irrigator",
                "add",
                "--cluster",
                "1",
                "--device-id",
                "fake_tuya_device_aabbccdd",
                "--name",
                "Tank pump",
                "--type",
                "rainpoint.ik10pw",
            ],
        )
        assert result.exit_code == 0
        body = captured[0]["json"]
        assert "reservoir_l" not in body
        assert "flow_rate_l_per_min" not in body

    def test_update_forwards_capacity_fields(self, patch_capture):
        """``irrigator update`` forwards the reservoir and flow-rate options."""
        captured = patch_capture({("PUT", "/api/v1/clusters/1/irrigator"): (200, {"id": 7})})
        result = runner.invoke(
            app,
            [
                "irrigator",
                "update",
                "1",
                "--reservoir-l",
                "15.5",
                "--flow-rate-l-per-min",
                "2",
            ],
        )
        assert result.exit_code == 0
        body = captured[0]["json"]
        assert body["reservoir_l"] == UPDATE_RESERVOIR_L
        assert body["flow_rate_l_per_min"] == UPDATE_FLOW_L_PER_MIN

    def test_update_omits_capacity_when_unset(self, patch_capture):
        """``irrigator update`` leaves the capacity fields out when they are not given."""
        captured = patch_capture({("PUT", "/api/v1/clusters/1/irrigator"): (200, {"id": 7})})
        result = runner.invoke(app, ["irrigator", "update", "1", "--name", "Renamed"])
        assert result.exit_code == 0
        body = captured[0]["json"]
        assert body == {"name": "Renamed"}


class TestIrrigatorCommands:
    """Cluster-keyed irrigator CRUD hits the singular routes."""

    def test_list_global(self, patch_client):
        """``irrigator list`` without a cluster lists every irrigator."""
        patch_client({("GET", "/api/v1/irrigators"): (200, [{"id": 7, "name": "Pump", "cluster_id": 1}])})
        result = runner.invoke(app, ["irrigator", "list"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data[0]["id"] == STUB_IRRIGATOR_ID

    def test_show(self, patch_capture):
        """``irrigator show`` reads the cluster's irrigator."""
        captured = patch_capture({("GET", "/api/v1/clusters/1/irrigator"): (200, {"id": 7, "name": "Pump"})})
        result = runner.invoke(app, ["irrigator", "show", "1"])
        assert result.exit_code == 0
        assert captured[0]["path"] == "/api/v1/clusters/1/irrigator"
        assert captured[0]["method"] == "GET"
        assert "Pump" in result.stdout

    def test_show_404_exits_nonzero(self, patch_capture):
        """A missing irrigator exits 1 with the server's message."""
        patch_capture({("GET", "/api/v1/clusters/9/irrigator"): (404, {"detail": "no irrigator"})})
        result = runner.invoke(app, ["irrigator", "show", "9"])
        assert result.exit_code == 1
        assert "no irrigator" in result.stderr

    def test_add_conflict_exits_nonzero(self, patch_capture):
        """Adding a second irrigator to a cluster exits 1 on the server's 409."""
        patch_capture({("POST", "/api/v1/clusters/1/irrigator"): (409, {"detail": "cluster already has an irrigator"})})
        result = runner.invoke(
            app,
            [
                "irrigator",
                "add",
                "--cluster",
                "1",
                "--device-id",
                "fake_tuya_device_aabbccdd",
                "--name",
                "Second pump",
                "--type",
                "rainpoint.ik10pw",
            ],
        )
        assert result.exit_code == 1
        assert "already has an irrigator" in result.stderr

    def test_update_targets_cluster_path(self, patch_capture):
        """``irrigator update`` addresses the irrigator through its cluster."""
        captured = patch_capture({("PUT", "/api/v1/clusters/1/irrigator"): (200, {"id": 7})})
        result = runner.invoke(app, ["irrigator", "update", "1", "--name", "Renamed"])
        assert result.exit_code == 0
        assert captured[0]["path"] == "/api/v1/clusters/1/irrigator"

    def test_update_404_exits_nonzero(self, patch_capture):
        """Updating a missing irrigator exits 1."""
        patch_capture({("PUT", "/api/v1/clusters/9/irrigator"): (404, {"detail": "no irrigator"})})
        result = runner.invoke(app, ["irrigator", "update", "9", "--name", "Renamed"])
        assert result.exit_code == 1

    def test_delete_targets_cluster_path(self, patch_capture):
        """``irrigator delete`` sends a DELETE on the cluster's irrigator path."""
        captured = patch_capture({("DELETE", "/api/v1/clusters/1/irrigator"): (200, {"deleted": True})})
        result = runner.invoke(app, ["irrigator", "delete", "1", "--yes"])
        assert result.exit_code == 0
        assert captured[0]["method"] == "DELETE"
        assert captured[0]["path"] == "/api/v1/clusters/1/irrigator"

    def test_delete_404_exits_nonzero(self, patch_capture):
        """Deleting a missing irrigator exits 1."""
        patch_capture({("DELETE", "/api/v1/clusters/9/irrigator"): (404, {"detail": "no irrigator"})})
        result = runner.invoke(app, ["irrigator", "delete", "9", "--yes"])
        assert result.exit_code == 1

    def test_start_unchanged_by_id(self, patch_capture):
        """``irrigator start`` still addresses the irrigator by its own id."""
        captured = patch_capture({("POST", "/api/v1/irrigators/7/start"): (200, {"status": "ok"})})
        result = runner.invoke(app, ["irrigator", "start", "7", "--minutes", "5"])
        assert result.exit_code == 0
        assert captured[0]["path"] == "/api/v1/irrigators/7/start"
