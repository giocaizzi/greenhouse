"""Functional tests for the Typer CLI client.

Uses Typer's CliRunner to invoke commands and httpx MockTransport
to simulate server responses without a running server.
"""

import json
from collections.abc import Callable
from http import HTTPStatus
from typing import Any, TypedDict

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

# Canned answers: ``(method, path) -> (status, body)``; a ``str`` body is served as CSV.
Routes = dict[tuple[str, str], tuple[int, object]]
PatchClient = Callable[[Routes], None]


class CapturedRequest(TypedDict):
    """One request the CLI sent, as ``patch_capture`` records it."""

    method: str
    path: str
    json: Any  # the parsed body exactly as ``json.loads`` returns it (None without a body)


PatchCapture = Callable[[Routes], list[CapturedRequest]]


def _mock_transport(routes: Routes) -> httpx.MockTransport:
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
def patch_client(monkeypatch: pytest.MonkeyPatch) -> PatchClient:
    """Factory fixture: patches IrrigationClient to use mock transport."""

    def _patch(routes: Routes) -> None:
        transport = _mock_transport(routes)

        original_init = httpx.Client.__init__

        def patched_init(self: httpx.Client, *_args: object, **kwargs: object) -> None:
            kwargs.pop("base_url", None)
            kwargs.pop("timeout", None)
            original_init(self, base_url="http://test", transport=transport, timeout=30.0)

        monkeypatch.setattr(httpx.Client, "__init__", patched_init)

    return _patch


class TestClusterCommands:
    """``greenhouse cluster`` sub-app requests and output."""


class TestOperationCommands:
    """Top-level operation commands (status, irrigate, check, monitor, sync, history, health)."""


class TestPlantCommands:
    """``greenhouse plant move`` outcomes."""

    def test_plant_move_missing_plant(self, patch_client: PatchClient) -> None:
        """A 404 for an unknown plant exits 1 with the server's message."""
        patch_client(
            {
                ("POST", "/api/v1/plants/999/move"): (404, {"detail": "Plant not found"}),
            }
        )
        result = runner.invoke(app, ["plant", "move", "999", "--to-cluster", "3"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_plant_move_same_cluster(self, patch_client: PatchClient) -> None:
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


class TestClientLifecycle:
    """The HTTP client is always closed after a command."""

    @pytest.mark.parametrize(("status", "exit_code"), [(200, 0), (404, 1)])
    def test_call_closes_the_client_it_opened(
        self, monkeypatch: pytest.MonkeyPatch, status: int, exit_code: int
    ) -> None:
        """Every CLI call closes its HTTP client, on success and on a server error."""
        body = {"status": "ok"} if status == HTTPStatus.OK else {"detail": "Not found"}
        transport = httpx.MockTransport(lambda _request: httpx.Response(status, json=body))
        clients: list[httpx.Client] = []
        original_init: Callable[..., None] = httpx.Client.__init__

        def recording_init(self: httpx.Client, *args: object, **kwargs: object) -> None:
            original_init(self, *args, **{**kwargs, "transport": transport})
            clients.append(self)

        monkeypatch.setattr(httpx.Client, "__init__", recording_init)
        result = runner.invoke(app, ["health"])
        assert result.exit_code == exit_code
        assert len(clients) == 1
        assert clients[0].is_closed


class TestServerFlag:
    """The root ``--server`` option."""


class TestSchedulerCommands:
    """``greenhouse scheduler`` sub-app."""

    def test_pause_server_error_exits_1(self, patch_client: PatchClient) -> None:
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
def patch_capture(monkeypatch: pytest.MonkeyPatch) -> PatchCapture:
    """Patch IrrigationClient and capture each request's (method, path, json body).

    Returns the list the handler appends to so a test can assert exactly which
    fields the CLI forwarded to the server.
    """
    captured: list[CapturedRequest] = []

    def _patch(routes: Routes) -> list[CapturedRequest]:
        def handler(request: httpx.Request) -> httpx.Response:
            body = None
            if request.content:
                body = json.loads(request.content)
            captured.append({"method": request.method, "path": request.url.path, "json": body})
            status, payload = routes.get((request.method, request.url.path), (404, {"detail": "Not found"}))
            return httpx.Response(status, json=payload)

        transport = httpx.MockTransport(handler)
        original_init = httpx.Client.__init__

        def patched_init(self: httpx.Client, *_args: object, **kwargs: object) -> None:
            kwargs.pop("base_url", None)
            kwargs.pop("timeout", None)
            original_init(self, base_url="http://test", transport=transport, timeout=30.0)

        monkeypatch.setattr(httpx.Client, "__init__", patched_init)
        return captured

    return _patch


class TestConfigCommands:
    """``greenhouse config`` and ``config global`` sub-apps."""

    def test_set_forwards_quiet_hours_and_caps(self, patch_capture: PatchCapture) -> None:
        """``config set`` forwards quiet hours and the daily cap under their API field names."""
        captured = patch_capture({("PUT", "/api/v1/clusters/1/config"): (200, {"id": 1, "cluster_id": 1})})
        result = runner.invoke(
            app,
            ["config", "set", "--cluster", "1", "--quiet-start", "0", "--quiet-end", "5", "--daily-cap", "30"],
        )
        assert result.exit_code == 0
        assert captured[0]["json"] == {"quiet_start_hour": 0, "quiet_end_hour": 5, "daily_cap_minutes": 30}

    def test_set_omits_unset_fields(self, patch_capture: PatchCapture) -> None:
        """Options left unset are not sent, so the server keeps their current values."""
        captured = patch_capture({("PUT", "/api/v1/clusters/1/config"): (200, {"id": 1, "cluster_id": 1})})
        result = runner.invoke(app, ["config", "set", "--cluster", "1", "--mode", "smart"])
        assert result.exit_code == 0
        # null-valued options are dropped so the server leaves them unchanged
        assert captured[0]["json"] == {"mode": "smart"}

    def test_effective_reads_merged_config(self, patch_client: PatchClient) -> None:
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

    def test_global_set_forwards_only_given_fields(self, patch_capture: PatchCapture) -> None:
        """``config global set`` sends only the fields given on the command line."""
        captured = patch_capture({("PUT", "/api/v1/config/global"): (200, {"id": 1})})
        result = runner.invoke(app, ["config", "global", "set", "--quiet-start", "1", "--quiet-end", "6"])
        assert result.exit_code == 0
        assert captured[0]["json"] == {"quiet_start_hour": 1, "quiet_end_hour": 6}


class TestIrrigateForce:
    """The ``irrigate --force`` flag."""


class TestIrrigatorCapacity:
    """Irrigator add/update forward the reservoir + flow-rate capacity fields."""


class TestIrrigatorCommands:
    """Cluster-keyed irrigator CRUD hits the singular routes."""

    def test_show(self, patch_capture: PatchCapture) -> None:
        """``irrigator show`` reads the cluster's irrigator."""
        captured = patch_capture({("GET", "/api/v1/clusters/1/irrigator"): (200, {"id": 7, "name": "Pump"})})
        result = runner.invoke(app, ["irrigator", "show", "1"])
        assert result.exit_code == 0
        assert captured[0]["path"] == "/api/v1/clusters/1/irrigator"
        assert captured[0]["method"] == "GET"
        assert "Pump" in result.stdout

    def test_show_404_exits_nonzero(self, patch_capture: PatchCapture) -> None:
        """A missing irrigator exits 1 with the server's message."""
        patch_capture({("GET", "/api/v1/clusters/9/irrigator"): (404, {"detail": "no irrigator"})})
        result = runner.invoke(app, ["irrigator", "show", "9"])
        assert result.exit_code == 1
        assert "no irrigator" in result.stderr

    def test_add_conflict_exits_nonzero(self, patch_capture: PatchCapture) -> None:
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

    def test_update_targets_cluster_path(self, patch_capture: PatchCapture) -> None:
        """``irrigator update`` addresses the irrigator through its cluster."""
        captured = patch_capture({("PUT", "/api/v1/clusters/1/irrigator"): (200, {"id": 7})})
        result = runner.invoke(app, ["irrigator", "update", "1", "--name", "Renamed"])
        assert result.exit_code == 0
        assert captured[0]["path"] == "/api/v1/clusters/1/irrigator"

    def test_update_404_exits_nonzero(self, patch_capture: PatchCapture) -> None:
        """Updating a missing irrigator exits 1."""
        patch_capture({("PUT", "/api/v1/clusters/9/irrigator"): (404, {"detail": "no irrigator"})})
        result = runner.invoke(app, ["irrigator", "update", "9", "--name", "Renamed"])
        assert result.exit_code == 1

    def test_delete_targets_cluster_path(self, patch_capture: PatchCapture) -> None:
        """``irrigator delete`` sends a DELETE on the cluster's irrigator path."""
        captured = patch_capture({("DELETE", "/api/v1/clusters/1/irrigator"): (200, {"deleted": True})})
        result = runner.invoke(app, ["irrigator", "delete", "1", "--yes"])
        assert result.exit_code == 0
        assert captured[0]["method"] == "DELETE"
        assert captured[0]["path"] == "/api/v1/clusters/1/irrigator"

    def test_delete_404_exits_nonzero(self, patch_capture: PatchCapture) -> None:
        """Deleting a missing irrigator exits 1."""
        patch_capture({("DELETE", "/api/v1/clusters/9/irrigator"): (404, {"detail": "no irrigator"})})
        result = runner.invoke(app, ["irrigator", "delete", "9", "--yes"])
        assert result.exit_code == 1

    def test_start_unchanged_by_id(self, patch_capture: PatchCapture) -> None:
        """``irrigator start`` still addresses the irrigator by its own id."""
        captured = patch_capture({("POST", "/api/v1/irrigators/7/start"): (200, {"status": "ok"})})
        result = runner.invoke(app, ["irrigator", "start", "7", "--minutes", "5"])
        assert result.exit_code == 0
        assert captured[0]["path"] == "/api/v1/irrigators/7/start"
