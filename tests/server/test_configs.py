"""Functional tests for irrigation config via HTTP."""

from http import HTTPStatus

from greenhouse_core.constants import DEFAULT_INTERVAL_HOURS

SEEDED_QUIET_END_HOUR = 5  # the Alembic migration seeds quiet hours 0-5


class TestConfigCRUD:
    """Tests for config CRUD."""

    def test_set_and_get(self, client):
        """Set and get."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        config = {"mode": "smart", "duration_minutes": 3, "interval_hours": 8, "auto_run": True}
        resp = client.put("/api/v1/clusters/1/config", json=config)
        assert resp.status_code == HTTPStatus.OK

        resp = client.get("/api/v1/clusters/1/config")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["mode"] == "smart"
        assert data["duration_minutes"] == config["duration_minutes"]
        assert data["auto_run"] is True

    def test_update_config(self, client):
        """Update config."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.put("/api/v1/clusters/1/config", json={"mode": "manual"})
        update = {"mode": "smart", "duration_minutes": 5}
        client.put("/api/v1/clusters/1/config", json=update)
        resp = client.get("/api/v1/clusters/1/config")
        assert resp.json()["mode"] == "smart"
        assert resp.json()["duration_minutes"] == update["duration_minutes"]

    def test_get_config_not_set(self, client):
        """Get config not set."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.get("/api/v1/clusters/1/config")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_set_config_partial(self, client):
        """Set config partial."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.put("/api/v1/clusters/1/config", json={"mode": "schedule"})
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["mode"] == "schedule"
        assert data["duration_minutes"] is None


class TestGlobalConfig:
    """Singleton global irrigation defaults: read + partial PUT semantics."""

    def test_get_returns_migration_seeded_quiet_hours(self, client):
        """The Alembic migration seeds the singleton row with quiet hours 0-5."""
        resp = client.get("/api/v1/config/global")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["quiet_start_hour"] == 0
        assert data["quiet_end_hour"] == SEEDED_QUIET_END_HOUR

    def test_update_global_quiet_hours(self, client):
        """Update global quiet hours."""
        quiet = {"quiet_start_hour": 0, "quiet_end_hour": 5}
        resp = client.put("/api/v1/config/global", json=quiet)
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert {key: data[key] for key in quiet} == quiet

    def test_clear_global_value_with_null(self, client):
        """Clear global value with null."""
        client.put("/api/v1/config/global", json={"duration_minutes": 4})
        resp = client.put("/api/v1/config/global", json={"duration_minutes": None})
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["duration_minutes"] is None

    def test_omitted_field_unchanged(self, client):
        """Omitted field unchanged."""
        first = {"duration_minutes": 4, "interval_hours": 9}
        second = {"interval_hours": 12}
        client.put("/api/v1/config/global", json=first)
        resp = client.put("/api/v1/config/global", json=second)
        data = resp.json()
        # duration_minutes was omitted from the second PUT — must stay as 4.
        assert data["duration_minutes"] == first["duration_minutes"]
        assert data["interval_hours"] == second["interval_hours"]


class TestEffectiveConfig:
    """Hierarchical resolution: cluster → global → built-in default."""

    def test_resolves_cluster_value(self, client):
        """Resolves cluster value."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.put("/api/v1/clusters/1/config", json={"mode": "manual", "duration_minutes": 7})

        resp = client.get("/api/v1/clusters/1/config/effective")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["effective"]["mode"] == {"value": "manual", "source": "cluster"}
        assert data["effective"]["duration_minutes"] == {"value": 7, "source": "cluster"}

    def test_resolves_global_default(self, client):
        """Resolves global default."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.put("/api/v1/clusters/1/config", json={"mode": "smart"})  # creates the row with only mode set
        client.put("/api/v1/config/global", json={"duration_minutes": 9})

        resp = client.get("/api/v1/clusters/1/config/effective")
        data = resp.json()
        assert data["effective"]["duration_minutes"] == {"value": 9, "source": "global"}

    def test_resolves_built_in_default(self, client):
        """Resolves built in default."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        # No cluster config row, no global override.
        resp = client.get("/api/v1/clusters/1/config/effective")
        data = resp.json()
        # interval_hours has no global override, no cluster row → built-in.
        assert data["effective"]["interval_hours"]["source"] == "default"
        assert data["effective"]["interval_hours"]["value"] == DEFAULT_INTERVAL_HOURS

    def test_404_when_cluster_missing(self, client):
        """404 when cluster missing."""
        resp = client.get("/api/v1/clusters/999/config/effective")
        assert resp.status_code == HTTPStatus.NOT_FOUND
