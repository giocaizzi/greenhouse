"""Functional tests for the /api/v1/preferences endpoint."""

from http import HTTPStatus

from fastapi.testclient import TestClient

DEFAULT_REFRESH_INTERVAL_SECONDS = 30


class TestGetPreferences:
    """Tests for get preferences."""

    def test_defaults_exist(self, client: TestClient) -> None:
        """GET preferences always returns a row with sensible defaults."""
        resp = client.get("/api/v1/preferences")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["units"] == "metric"
        assert data["timezone"] == "UTC"
        assert data["theme"] == "auto"
        assert data["default_cluster_id"] is None
        assert data["refresh_interval_seconds"] == DEFAULT_REFRESH_INTERVAL_SECONDS
        assert data["dry_run_global"] is False
        # ntfy per-category toggles default ON.
        assert data["notify_manual"] is True
        assert data["notify_emergency"] is True
        assert data["notify_alerts"] is True
        assert data["notify_auto"] is True

    def test_idempotent(self, client: TestClient) -> None:
        """Multiple GET calls return the same defaults without creating duplicates."""
        r1 = client.get("/api/v1/preferences")
        r2 = client.get("/api/v1/preferences")
        assert r1.json() == r2.json()


class TestUpdatePreferences:
    """Tests for update preferences."""

    def test_put_full(self, client: TestClient) -> None:
        """PUT with all fields updates every preference."""
        update = {
            "units": "imperial",
            "timezone": "America/New_York",
            "theme": "dark",
            "refresh_interval_seconds": 60,
            "dry_run_global": True,
        }
        resp = client.put("/api/v1/preferences", json=update)
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["units"] == "imperial"
        assert data["timezone"] == "America/New_York"
        assert data["theme"] == "dark"
        assert data["refresh_interval_seconds"] == update["refresh_interval_seconds"]
        assert data["dry_run_global"] is True

    def test_put_partial_preserves_other_fields(self, client: TestClient) -> None:
        """PUT with a subset of fields must not reset the others."""
        # Set all first
        client.put("/api/v1/preferences", json={"units": "imperial", "theme": "dark"})
        # Partial update — only timezone
        resp = client.put("/api/v1/preferences", json={"timezone": "Europe/Rome"})
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["units"] == "imperial"  # preserved
        assert data["theme"] == "dark"  # preserved
        assert data["timezone"] == "Europe/Rome"

    def test_get_reflects_put(self, client: TestClient) -> None:
        """GET after PUT returns the updated values."""
        client.put("/api/v1/preferences", json={"dry_run_global": True})
        resp = client.get("/api/v1/preferences")
        assert resp.json()["dry_run_global"] is True

    def test_put_notify_flags(self, client: TestClient) -> None:
        """PUT can toggle individual ntfy notification categories."""
        resp = client.put("/api/v1/preferences", json={"notify_auto": False, "notify_alerts": False})
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["notify_auto"] is False
        assert data["notify_alerts"] is False
        # Untouched categories stay enabled.
        assert data["notify_manual"] is True
        assert data["notify_emergency"] is True
