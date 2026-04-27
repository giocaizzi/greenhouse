"""Functional tests for the /api/v1/vacation endpoint."""

import time


class TestListVacationWindows:
    def test_list_empty(self, client):
        """GET vacation with no windows returns empty list and null active."""
        resp = client.get("/api/v1/vacation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is None
        assert data["items"] == []


class TestCreateVacationWindow:
    def test_create_basic(self, client):
        """POST creates a vacation window and returns it."""
        now = int(time.time())
        resp = client.post(
            "/api/v1/vacation",
            json={"starts_at": now - 100, "ends_at": now + 3600},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == 1
        assert data["starts_at"] == now - 100
        assert data["ends_at"] == now + 3600
        assert data["contact_email"] is None

    def test_create_with_all_fields(self, client):
        """POST with optional fields stores them correctly."""
        now = int(time.time())
        resp = client.post(
            "/api/v1/vacation",
            json={
                "starts_at": now + 86400,
                "ends_at": now + 172800,
                "contact_email": "test@example.com",
                "notes": "Two-week trip",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["contact_email"] == "test@example.com"
        assert data["notes"] == "Two-week trip"


class TestActiveVacationFlag:
    def test_active_window_during_current_time(self, client):
        """A window spanning now should appear as active."""
        now = int(time.time())
        client.post("/api/v1/vacation", json={"starts_at": now - 3600, "ends_at": now + 3600})
        resp = client.get("/api/v1/vacation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is not None
        assert data["active"]["id"] == 1

    def test_future_window_not_active(self, client):
        """A window in the future must not appear as active."""
        now = int(time.time())
        client.post("/api/v1/vacation", json={"starts_at": now + 7200, "ends_at": now + 86400})
        resp = client.get("/api/v1/vacation")
        assert resp.json()["active"] is None

    def test_past_window_not_active(self, client):
        """A window that already ended must not appear as active."""
        now = int(time.time())
        client.post("/api/v1/vacation", json={"starts_at": now - 7200, "ends_at": now - 3600})
        resp = client.get("/api/v1/vacation")
        assert resp.json()["active"] is None

    def test_list_includes_all_windows(self, client):
        """items list contains both active and future windows."""
        now = int(time.time())
        client.post("/api/v1/vacation", json={"starts_at": now - 3600, "ends_at": now + 3600})
        client.post("/api/v1/vacation", json={"starts_at": now + 7200, "ends_at": now + 86400})
        resp = client.get("/api/v1/vacation")
        assert len(resp.json()["items"]) == 2


class TestDeleteVacationWindow:
    def test_delete_existing(self, client):
        """DELETE removes the window and returns success."""
        now = int(time.time())
        client.post("/api/v1/vacation", json={"starts_at": now + 100, "ends_at": now + 200})
        resp = client.delete("/api/v1/vacation/1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        resp = client.get("/api/v1/vacation")
        assert resp.json()["items"] == []

    def test_delete_not_found(self, client):
        """DELETE on a non-existent ID returns 404."""
        resp = client.delete("/api/v1/vacation/999")
        assert resp.status_code == 404
