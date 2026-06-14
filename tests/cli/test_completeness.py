"""Tests for the CLI commands added to close API/CLI gaps.

Same pattern as ``test_cli.py``: Typer ``CliRunner`` driving the app, with
``httpx.MockTransport`` patched onto ``httpx.Client.__init__`` so the client
hits an in-memory server and the request shape can be asserted.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from greenhouse_cli import client as client_mod
from greenhouse_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_token_dir(tmp_path, monkeypatch):
    """Redirect on-disk token storage to a temp dir so tests don't touch real config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("GREENHOUSE_API_TOKEN", raising=False)
    yield


def _mock(routes: dict, captured: list | None = None):
    """Build a MockTransport that returns canned responses and records calls."""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            try:
                body = json.loads(request.content) if request.content else None
            except json.JSONDecodeError:
                body = request.content.decode("utf-8", errors="replace")
            captured.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "params": dict(request.url.params),
                    "json": body,
                    "headers": dict(request.headers),
                }
            )
        key = (request.method, request.url.path)
        if key in routes:
            status, body = routes[key]
            return httpx.Response(status, json=body)
        return httpx.Response(404, json={"detail": "Not found"})

    return httpx.MockTransport(handler)


@pytest.fixture
def patch_client(monkeypatch) -> Callable[[dict, list | None], None]:
    """Patch ``httpx.Client.__init__`` to use a MockTransport with the given routes."""

    def _patch(routes: dict, captured: list | None = None) -> None:
        transport = _mock(routes, captured)
        original_init = httpx.Client.__init__

        def patched_init(self, *args, **kwargs):
            kwargs.pop("base_url", None)
            kwargs.pop("timeout", None)
            headers = kwargs.pop("headers", None)
            original_init(
                self,
                base_url="http://test",
                transport=transport,
                timeout=30.0,
                headers=headers,
            )

        monkeypatch.setattr(httpx.Client, "__init__", patched_init)

    return _patch


# ─────────────────────────── auth ────────────────────────────


class TestAuthCommands:
    def test_login_stores_token(self, patch_client, tmp_path):
        captured: list = []
        patch_client(
            {
                ("POST", "/api/v1/auth/login"): (
                    200,
                    {
                        "access_token": "jwt-abc",
                        "token_type": "bearer",
                        "expires_in": 3600,
                        "username": "alice",
                    },
                ),
            },
            captured,
        )
        result = runner.invoke(app, ["login", "--username", "alice", "--password", "s3cret"])
        assert result.exit_code == 0, result.output
        assert "alice" in result.output

        assert captured[0]["method"] == "POST"
        assert captured[0]["json"] == {"username": "alice", "password": "s3cret"}

        token_path = Path(os.environ["XDG_CONFIG_HOME"]) / "greenhouse" / "token"
        assert token_path.read_text() == "jwt-abc"
        # 0o600 mode bits
        assert (token_path.stat().st_mode & 0o777) == 0o600

    def test_login_print_token_does_not_persist(self, patch_client):
        patch_client(
            {
                ("POST", "/api/v1/auth/login"): (
                    200,
                    {
                        "access_token": "jwt-zzz",
                        "token_type": "bearer",
                        "expires_in": 60,
                        "username": "bob",
                    },
                ),
            }
        )
        result = runner.invoke(
            app,
            ["login", "--username", "bob", "--password", "pw", "--print-token"],
        )
        assert result.exit_code == 0
        assert "jwt-zzz" in result.output
        token_path = Path(os.environ["XDG_CONFIG_HOME"]) / "greenhouse" / "token"
        assert not token_path.exists()

    def test_login_failure_exits_1(self, patch_client):
        patch_client(
            {
                ("POST", "/api/v1/auth/login"): (401, {"detail": "Invalid username or password"}),
            }
        )
        result = runner.invoke(app, ["login", "--username", "x", "--password", "bad"])
        assert result.exit_code == 1
        assert "invalid" in result.output.lower()

    def test_logout_removes_token(self, patch_client):
        client_mod.store_token("jwt-here")
        patch_client(
            {
                ("POST", "/api/v1/auth/logout"): (200, {"detail": "Logged out"}),
            }
        )
        result = runner.invoke(app, ["logout"])
        assert result.exit_code == 0
        token_path = Path(os.environ["XDG_CONFIG_HOME"]) / "greenhouse" / "token"
        assert not token_path.exists()

    def test_logout_no_token(self, patch_client):
        patch_client(
            {
                ("POST", "/api/v1/auth/logout"): (401, {"detail": "Not authenticated"}),
            }
        )
        result = runner.invoke(app, ["logout"])
        assert result.exit_code == 0
        assert "no token" in result.output.lower()

    def test_whoami(self, patch_client):
        client_mod.store_token("jwt-stored")
        captured: list = []
        patch_client(
            {
                ("GET", "/api/v1/auth/me"): (200, {"id": 1, "username": "alice"}),
            },
            captured,
        )
        result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["username"] == "alice"
        assert captured[0]["headers"].get("authorization") == "Bearer jwt-stored"

    def test_env_token_wins_over_disk(self, patch_client, monkeypatch):
        client_mod.store_token("disk-token")
        monkeypatch.setenv("GREENHOUSE_API_TOKEN", "env-token")
        captured: list = []
        patch_client(
            {
                ("GET", "/api/v1/auth/me"): (200, {"id": 1, "username": "alice"}),
            },
            captured,
        )
        result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 0
        assert captured[0]["headers"].get("authorization") == "Bearer env-token"


# ─────────────────────────── alerts ───────────────────────────


class TestAlertsCommands:
    def _stub_alert(self, alert_id: int = 1, status: str = "open") -> dict:
        return {
            "id": alert_id,
            "source": "engine",
            "code": "dry_streak",
            "severity": "warning",
            "entity_type": "cluster",
            "entity_id": 1,
            "cluster_id": 1,
            "plant_id": None,
            "title": "Dry",
            "message": "moist <30",
            "status": status,
            "first_seen_at": 0,
            "last_seen_at": 0,
            "occurrence_count": 1,
            "acknowledged_at": None,
            "resolved_at": None,
        }

    def test_list(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("GET", "/api/v1/alerts"): (
                    200,
                    {"open_count": 1, "items": [self._stub_alert()]},
                ),
            },
            captured,
        )
        result = runner.invoke(app, ["alerts", "list", "--status", "open", "--cluster", "1"])
        assert result.exit_code == 0
        assert captured[0]["params"] == {"limit": "100", "status": "open", "cluster_id": "1"}

    def test_get(self, patch_client):
        patch_client(
            {
                ("GET", "/api/v1/alerts/42"): (200, self._stub_alert(42)),
            }
        )
        result = runner.invoke(app, ["alerts", "get", "42"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["id"] == 42

    def test_ack(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("POST", "/api/v1/alerts/5/acknowledge"): (200, self._stub_alert(5, status="acknowledged")),
            },
            captured,
        )
        result = runner.invoke(app, ["alerts", "ack", "5"])
        assert result.exit_code == 0
        assert captured[0]["method"] == "POST"

    def test_resolve(self, patch_client):
        patch_client(
            {
                ("POST", "/api/v1/alerts/5/resolve"): (200, self._stub_alert(5, status="resolved")),
            }
        )
        result = runner.invoke(app, ["alerts", "resolve", "5"])
        assert result.exit_code == 0

    def test_sync_all(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("POST", "/api/v1/alerts/sync"): (200, {"open_count": 0, "items": []}),
            },
            captured,
        )
        result = runner.invoke(app, ["alerts", "sync"])
        assert result.exit_code == 0
        assert captured[0]["path"] == "/api/v1/alerts/sync"

    def test_sync_cluster(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("POST", "/api/v1/clusters/3/alerts/sync"): (200, {"open_count": 0, "items": []}),
            },
            captured,
        )
        result = runner.invoke(app, ["alerts", "sync", "--cluster", "3"])
        assert result.exit_code == 0
        assert captured[0]["path"] == "/api/v1/clusters/3/alerts/sync"


# ─────────────────────────── decisions ──────────────────────────


class TestDecisionsCommands:
    def test_list(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("GET", "/api/v1/clusters/1/decisions"): (
                    200,
                    {"cluster_id": 1, "items": []},
                ),
            },
            captured,
        )
        result = runner.invoke(app, ["decisions", "list", "--cluster", "1", "--limit", "10"])
        assert result.exit_code == 0
        assert captured[0]["params"] == {"limit": "10"}


# ─────────────────────────── preferences ────────────────────────


class TestPreferencesCommands:
    def _stub(self) -> dict:
        return {
            "units": "metric",
            "timezone": "Europe/Rome",
            "theme": "auto",
            "default_cluster_id": None,
            "refresh_interval_seconds": 60,
            "dry_run_global": False,
            "scheduler_paused": False,
        }

    def test_get(self, patch_client):
        patch_client(
            {
                ("GET", "/api/v1/preferences"): (200, self._stub()),
            }
        )
        result = runner.invoke(app, ["prefs", "get"])
        assert result.exit_code == 0
        assert "metric" in result.stdout

    def test_set_only_supplied_fields_are_sent(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("PUT", "/api/v1/preferences"): (200, self._stub()),
            },
            captured,
        )
        result = runner.invoke(
            app,
            ["prefs", "set", "--theme", "dark", "--refresh-interval", "30"],
        )
        assert result.exit_code == 0
        assert captured[0]["json"] == {"theme": "dark", "refresh_interval_seconds": 30}

    def test_set_dry_run_flag(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("PUT", "/api/v1/preferences"): (200, self._stub()),
            },
            captured,
        )
        result = runner.invoke(app, ["prefs", "set", "--dry-run-global"])
        assert result.exit_code == 0
        assert captured[0]["json"] == {"dry_run_global": True}


# ─────────────────────────── vacation ──────────────────────────


class TestVacationCommands:
    def _stub(self, wid: int = 1) -> dict:
        return {
            "id": wid,
            "starts_at": 1_700_000_000,
            "ends_at": 1_700_100_000,
            "contact_email": None,
            "notes": None,
            "created_at": 0,
        }

    def test_list(self, patch_client):
        patch_client(
            {
                ("GET", "/api/v1/vacation"): (
                    200,
                    {"active": None, "items": [self._stub()]},
                ),
            }
        )
        result = runner.invoke(app, ["vacation", "list"])
        assert result.exit_code == 0

    def test_add(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("POST", "/api/v1/vacation"): (201, self._stub()),
            },
            captured,
        )
        result = runner.invoke(
            app,
            [
                "vacation",
                "add",
                "--starts-at",
                "1700000000",
                "--ends-at",
                "1700100000",
                "--email",
                "g@example.com",
            ],
        )
        assert result.exit_code == 0
        assert captured[0]["json"] == {
            "starts_at": 1700000000,
            "ends_at": 1700100000,
            "contact_email": "g@example.com",
            "notes": None,
        }

    def test_update_optimistic(self, patch_client):
        """``vacation update`` targets PUT /vacation/{id} even though the
        endpoint is being added on a parallel branch — test the request shape."""
        captured: list = []
        patch_client(
            {
                ("PUT", "/api/v1/vacation/9"): (200, self._stub(9)),
            },
            captured,
        )
        result = runner.invoke(
            app,
            ["vacation", "update", "9", "--notes", "back Friday"],
        )
        assert result.exit_code == 0
        assert captured[0]["method"] == "PUT"
        assert captured[0]["json"] == {"notes": "back Friday"}

    def test_delete(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("DELETE", "/api/v1/vacation/9"): (200, {"success": True}),
            },
            captured,
        )
        result = runner.invoke(app, ["vacation", "delete", "9", "--yes"])
        assert result.exit_code == 0
        assert captured[0]["method"] == "DELETE"


# ─────────────────────────── windows ──────────────────────────


class TestWindowsCommands:
    def _stub(self) -> dict:
        return {
            "id": 1,
            "cluster_id": 2,
            "start_hour": 6,
            "end_hour": 10,
            "weekday_mask": 127,
            "label": "morning",
        }

    def test_list(self, patch_client):
        patch_client(
            {
                ("GET", "/api/v1/clusters/2/windows"): (
                    200,
                    {"cluster_id": 2, "windows": [self._stub()]},
                ),
            }
        )
        result = runner.invoke(app, ["windows", "list", "--cluster", "2"])
        assert result.exit_code == 0
        assert "morning" in result.stdout

    def test_add(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("POST", "/api/v1/clusters/2/windows"): (201, self._stub()),
            },
            captured,
        )
        result = runner.invoke(
            app,
            [
                "windows",
                "add",
                "--cluster",
                "2",
                "--start-hour",
                "6",
                "--end-hour",
                "10",
                "--label",
                "morning",
            ],
        )
        assert result.exit_code == 0
        assert captured[0]["json"] == {
            "start_hour": 6,
            "end_hour": 10,
            "weekday_mask": 127,
            "label": "morning",
        }

    def test_update_partial(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("PUT", "/api/v1/clusters/2/windows/1"): (200, self._stub()),
            },
            captured,
        )
        result = runner.invoke(
            app,
            ["windows", "update", "1", "--cluster", "2", "--label", "renamed"],
        )
        assert result.exit_code == 0
        assert captured[0]["json"] == {"label": "renamed"}

    def test_delete(self, patch_client):
        patch_client(
            {
                ("DELETE", "/api/v1/clusters/2/windows/1"): (200, {"success": True}),
            }
        )
        result = runner.invoke(app, ["windows", "delete", "1", "--cluster", "2", "--yes"])
        assert result.exit_code == 0


# ─────────────────────────── cluster CRUD ──────────────────────────


class TestClusterCrud:
    def test_get(self, patch_client):
        patch_client(
            {
                ("GET", "/api/v1/clusters/4"): (200, {"id": 4, "name": "Den", "environment": "indoor"}),
            }
        )
        result = runner.invoke(app, ["cluster", "get", "4"])
        assert result.exit_code == 0
        assert "Den" in result.stdout

    def test_update_sends_only_set_fields(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("PUT", "/api/v1/clusters/4"): (200, {"id": 4, "name": "Den2", "environment": "indoor"}),
            },
            captured,
        )
        result = runner.invoke(app, ["cluster", "update", "4", "--name", "Den2"])
        assert result.exit_code == 0
        assert captured[0]["json"] == {"name": "Den2"}

    def test_delete_with_yes(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("DELETE", "/api/v1/clusters/4"): (200, {"success": True}),
            },
            captured,
        )
        result = runner.invoke(app, ["cluster", "delete", "4", "--yes"])
        assert result.exit_code == 0
        assert captured[0]["method"] == "DELETE"

    def test_delete_prompt_aborts_without_yes(self, patch_client):
        patch_client(
            {
                ("DELETE", "/api/v1/clusters/4"): (200, {"success": True}),
            }
        )
        result = runner.invoke(app, ["cluster", "delete", "4"], input="n\n")
        # confirmation aborted
        assert result.exit_code != 0


# ─────────────────────────── plant/sensor/irrigator CRUD ──────────────────


class TestResourceCrud:
    def test_plant_update(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("PUT", "/api/v1/clusters/2/plants/9"): (
                    200,
                    {"id": 9, "cluster_id": 2, "species": "Fern v2"},
                ),
            },
            captured,
        )
        result = runner.invoke(
            app,
            ["plant", "update", "9", "--cluster", "2", "--species", "Fern v2"],
        )
        assert result.exit_code == 0
        assert captured[0]["json"] == {"species": "Fern v2"}

    def test_plant_delete(self, patch_client):
        patch_client(
            {
                ("DELETE", "/api/v1/clusters/2/plants/9"): (200, {"success": True}),
            }
        )
        result = runner.invoke(app, ["plant", "delete", "9", "--cluster", "2", "--yes"])
        assert result.exit_code == 0

    def test_irrigator_update_with_config(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("PUT", "/api/v1/clusters/1/irrigator"): (
                    200,
                    {"id": 4, "cluster_id": 1, "name": "renamed"},
                ),
            },
            captured,
        )
        result = runner.invoke(
            app,
            [
                "irrigator",
                "update",
                "1",
                "--name",
                "renamed",
                "--device-ip",
                "192.0.2.10",
                "--local-key",
                "k",
            ],
        )
        assert result.exit_code == 0
        assert captured[0]["json"] == {
            "name": "renamed",
            "config": {"device_ip": "192.0.2.10", "local_key": "k"},
        }

    def test_irrigator_delete(self, patch_client):
        patch_client(
            {
                ("DELETE", "/api/v1/clusters/1/irrigator"): (200, {"success": True}),
            }
        )
        result = runner.invoke(app, ["irrigator", "delete", "1", "--yes"])
        assert result.exit_code == 0

    def test_sensor_update(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("PUT", "/api/v1/clusters/1/sensors/3"): (
                    200,
                    {"id": 3, "cluster_id": 1, "plant_id": 7},
                ),
            },
            captured,
        )
        result = runner.invoke(
            app,
            ["sensor", "update", "3", "--cluster", "1", "--plant-id", "7"],
        )
        assert result.exit_code == 0
        assert captured[0]["json"] == {"plant_id": 7}

    def test_sensor_delete(self, patch_client):
        patch_client(
            {
                ("DELETE", "/api/v1/clusters/1/sensors/3"): (200, {"success": True}),
            }
        )
        result = runner.invoke(app, ["sensor", "delete", "3", "--cluster", "1", "--yes"])
        assert result.exit_code == 0


# ─────────────────────────── stop-all ──────────────────────────


class TestStopAllCommand:
    def test_stop_all_with_yes(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("POST", "/api/v1/bulk/stop-all"): (200, {"stopped": 3, "errors": []}),
            },
            captured,
        )
        result = runner.invoke(app, ["stop-all", "--yes"])
        assert result.exit_code == 0
        assert captured[0]["method"] == "POST"
        assert captured[0]["path"] == "/api/v1/bulk/stop-all"
        data = json.loads(result.stdout)
        assert data == {"stopped": 3, "errors": []}

    def test_stop_all_short_flag(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("POST", "/api/v1/bulk/stop-all"): (200, {"stopped": 0, "errors": []}),
            },
            captured,
        )
        result = runner.invoke(app, ["stop-all", "-y"])
        assert result.exit_code == 0
        assert captured[0]["path"] == "/api/v1/bulk/stop-all"

    def test_stop_all_prompt_aborts_without_yes(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("POST", "/api/v1/bulk/stop-all"): (200, {"stopped": 1, "errors": []}),
            },
            captured,
        )
        result = runner.invoke(app, ["stop-all"], input="n\n")
        assert result.exit_code != 0
        assert captured == []

    def test_stop_all_prompt_confirmed(self, patch_client):
        captured: list = []
        patch_client(
            {
                ("POST", "/api/v1/bulk/stop-all"): (200, {"stopped": 2, "errors": ["irrigator 5: timeout"]}),
            },
            captured,
        )
        result = runner.invoke(app, ["stop-all"], input="y\n")
        assert result.exit_code == 0
        assert captured[0]["path"] == "/api/v1/bulk/stop-all"
        assert '"stopped": 2' in result.stdout
        assert "irrigator 5: timeout" in result.stdout

    def test_stop_all_server_error_exits_1(self, patch_client):
        patch_client(
            {
                ("POST", "/api/v1/bulk/stop-all"): (500, {"detail": "Device manager down"}),
            }
        )
        result = runner.invoke(app, ["stop-all", "--yes"])
        assert result.exit_code == 1
        assert "device manager down" in result.output.lower()
