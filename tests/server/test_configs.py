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


class TestGlobalConfig:
    """Singleton global irrigation defaults: read + partial PUT semantics."""

    def test_get_returns_migration_seeded_quiet_hours(self, client):
        """The Alembic migration seeds the singleton row with quiet hours 0–5."""
        resp = client.get("/api/v1/config/global")
        assert resp.status_code == 200
        data = resp.json()
        assert data["quiet_start_hour"] == 0
        assert data["quiet_end_hour"] == 5

    def test_update_global_quiet_hours(self, client):
        resp = client.put("/api/v1/config/global", json={"quiet_start_hour": 0, "quiet_end_hour": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert data["quiet_start_hour"] == 0
        assert data["quiet_end_hour"] == 5

    def test_clear_global_value_with_null(self, client):
        client.put("/api/v1/config/global", json={"duration_minutes": 4})
        resp = client.put("/api/v1/config/global", json={"duration_minutes": None})
        assert resp.status_code == 200
        assert resp.json()["duration_minutes"] is None

    def test_omitted_field_unchanged(self, client):
        client.put("/api/v1/config/global", json={"duration_minutes": 4, "interval_hours": 9})
        resp = client.put("/api/v1/config/global", json={"interval_hours": 12})
        data = resp.json()
        # duration_minutes was omitted from the second PUT — must stay as 4.
        assert data["duration_minutes"] == 4
        assert data["interval_hours"] == 12


class TestEffectiveConfig:
    """Hierarchical resolution: cluster → global → built-in default."""

    def test_resolves_cluster_value(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.put("/api/v1/clusters/1/config", json={"mode": "manual", "duration_minutes": 7})

        resp = client.get("/api/v1/clusters/1/config/effective")
        assert resp.status_code == 200
        data = resp.json()
        assert data["effective"]["mode"] == {"value": "manual", "source": "cluster"}
        assert data["effective"]["duration_minutes"] == {"value": 7, "source": "cluster"}

    def test_resolves_global_default(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.put("/api/v1/clusters/1/config", json={"mode": "smart"})  # creates the row with only mode set
        client.put("/api/v1/config/global", json={"duration_minutes": 9})

        resp = client.get("/api/v1/clusters/1/config/effective")
        data = resp.json()
        assert data["effective"]["duration_minutes"] == {"value": 9, "source": "global"}

    def test_resolves_built_in_default(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        # No cluster config row, no global override.
        resp = client.get("/api/v1/clusters/1/config/effective")
        data = resp.json()
        # interval_hours has no global override, no cluster row → built-in.
        assert data["effective"]["interval_hours"]["source"] == "default"
        assert data["effective"]["interval_hours"]["value"] == 12  # DEFAULT_INTERVAL_HOURS

    def test_404_when_cluster_missing(self, client):
        resp = client.get("/api/v1/clusters/999/config/effective")
        assert resp.status_code == 404
