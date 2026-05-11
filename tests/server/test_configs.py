"""Functional tests for irrigation config via HTTP."""


class TestConfigCRUD:
    def test_set_and_get(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.put(
            "/api/v1/clusters/1/config",
            json={"mode": "smart", "duration_minutes": 3, "interval_hours": 8, "auto_run": True},
        )
        assert resp.status_code == 200

        resp = client.get("/api/v1/clusters/1/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "smart"
        assert data["duration_minutes"] == 3
        assert data["auto_run"] is True

    def test_update_config(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.put("/api/v1/clusters/1/config", json={"mode": "manual"})
        client.put("/api/v1/clusters/1/config", json={"mode": "smart", "duration_minutes": 5})
        resp = client.get("/api/v1/clusters/1/config")
        assert resp.json()["mode"] == "smart"
        assert resp.json()["duration_minutes"] == 5

    def test_get_config_not_set(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.get("/api/v1/clusters/1/config")
        assert resp.status_code == 404

    def test_set_config_partial(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.put("/api/v1/clusters/1/config", json={"mode": "schedule"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "schedule"
        assert data["duration_minutes"] is None
