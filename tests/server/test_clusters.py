"""Functional tests for cluster CRUD + operations via HTTP."""

import warnings
from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from golden import FROZEN_TS
from greenhouse_core.models import ActivitySource, EntityType
from greenhouse_core.repository import IrrigationRepository
from server.conftest import app_of


class TestClusterCRUD:
    """Tests for cluster CRUD."""

    def test_create_and_list(self, client: TestClient) -> None:
        """Create and list."""
        resp = client.post("/api/v1/clusters", json={"name": "My Cluster"})
        assert resp.status_code == HTTPStatus.CREATED
        data = resp.json()
        assert data["name"] == "My Cluster"
        assert data["environment"] == "indoor"
        assert data["id"] == 1

        resp = client.get("/api/v1/clusters")
        assert resp.status_code == HTTPStatus.OK
        assert len(resp.json()) == 1

    def test_create_with_all_fields(self, client: TestClient) -> None:
        """Create with all fields."""
        resp = client.post(
            "/api/v1/clusters",
            json={"name": "Outdoor", "location": "Balcony", "environment": "outdoor"},
        )
        assert resp.status_code == HTTPStatus.CREATED
        data = resp.json()
        assert data["location"] == "Balcony"
        assert data["environment"] == "outdoor"

    def test_get_by_id(self, client: TestClient) -> None:
        """Get by ID."""
        client.post("/api/v1/clusters", json={"name": "Test"})
        resp = client.get("/api/v1/clusters/1")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["name"] == "Test"

    def test_get_not_found(self, client: TestClient) -> None:
        """Get not found."""
        resp = client.get("/api/v1/clusters/999")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_list_empty(self, client: TestClient) -> None:
        """List empty."""
        resp = client.get("/api/v1/clusters")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json() == []

    def test_create_two_clusters(self, client: TestClient) -> None:
        """Create two clusters."""
        names = ["A", "B"]
        for name in names:
            client.post("/api/v1/clusters", json={"name": name})
        resp = client.get("/api/v1/clusters")
        assert len(resp.json()) == len(names)

    def test_update_name_and_location(self, client: TestClient) -> None:
        """Update name and location."""
        client.post("/api/v1/clusters", json={"name": "Original", "environment": "indoor"})
        resp = client.put("/api/v1/clusters/1", json={"name": "Renamed", "location": "Shelf"})
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["name"] == "Renamed"
        assert data["location"] == "Shelf"
        assert data["environment"] == "indoor"  # unchanged

    def test_update_reflects_on_get(self, client: TestClient) -> None:
        """Update reflects on get."""
        client.post("/api/v1/clusters", json={"name": "Before"})
        client.put("/api/v1/clusters/1", json={"name": "After"})
        resp = client.get("/api/v1/clusters/1")
        assert resp.json()["name"] == "After"

    def test_update_not_found(self, client: TestClient) -> None:
        """Update not found."""
        resp = client.put("/api/v1/clusters/999", json={"name": "X"})
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_delete_cluster(self, client: TestClient) -> None:
        """Delete cluster."""
        client.post("/api/v1/clusters", json={"name": "ToDelete"})
        resp = client.delete("/api/v1/clusters/1")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["success"] is True

        resp = client.get("/api/v1/clusters/1")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_delete_cascades_to_plants(self, client: TestClient) -> None:
        """Delete cascades to plants."""
        client.post("/api/v1/clusters", json={"name": "C"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        client.delete("/api/v1/clusters/1")
        resp = client.get("/api/v1/clusters/1")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_delete_removes_rows_that_point_at_the_cluster(self, client: TestClient) -> None:
        """Deleting a cluster removes its dependent rows and leaves other clusters alone.

        Windows, assignments, alerts (by cluster or entity) go; a default-cluster preference is cleared;
        another cluster's rows stay.
        """
        doomed = client.post("/api/v1/clusters", json={"name": "Doomed"}).json()["id"]
        kept = client.post("/api/v1/clusters", json={"name": "Kept"}).json()["id"]
        for cid in (doomed, kept):
            pid = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": "Fern"}).json()["id"]
            client.post(
                f"/api/v1/clusters/{cid}/sensors",
                json={
                    "tuya_device_id": f"fake_tuya_sensor_{cid:08d}",
                    "name": "S",
                    "type": "tuya.tr301z",
                    "plant_id": pid,
                },
            )
            client.post(f"/api/v1/clusters/{cid}/windows", json={"start_hour": 6, "end_hour": 9})
        client.put("/api/v1/preferences", json={"default_cluster_id": doomed})
        session = app_of(client).state.session_factory()
        repo = IrrigationRepository(session)
        doomed_sensor = repo.get_sensors_in_cluster(doomed)[0].id
        repo.upsert_alert(
            dedup_key="sensor-only", source=ActivitySource.SENSOR, code="offline", severity="warning",
            entity_type=EntityType.SENSOR, entity_id=doomed_sensor, title="t", message="m",
        )  # fmt: skip
        repo.commit()
        session.close()

        assert client.delete(f"/api/v1/clusters/{doomed}").status_code == HTTPStatus.OK

        engine = app_of(client).state.session_factory.kw["bind"]
        with engine.connect() as conn:
            counts = {
                table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()  # noqa: S608 — fixed names
                for table in ("irrigation_windows", "sensor_assignments", "alerts", "plants", "sensors")
            }
        assert counts == {"irrigation_windows": 1, "sensor_assignments": 1, "alerts": 0, "plants": 1, "sensors": 1}
        assert client.get("/api/v1/preferences").json()["default_cluster_id"] is None
        assert client.get(f"/api/v1/clusters/{kept}/windows").json()

    def test_delete_not_found(self, client: TestClient) -> None:
        """Delete not found."""
        resp = client.delete("/api/v1/clusters/999")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_double_delete_returns_404(self, client: TestClient) -> None:
        """Double delete returns 404."""
        client.post("/api/v1/clusters", json={"name": "Once"})
        client.delete("/api/v1/clusters/1")
        resp = client.delete("/api/v1/clusters/1")
        assert resp.status_code == HTTPStatus.NOT_FOUND


class TestClusterStatus:
    """Tests for cluster status."""

    def test_status_seeded(self, seeded_client: TestClient) -> None:
        """Status seeded."""
        resp = seeded_client.get("/api/v1/clusters/1/status")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["cluster"]["name"] == "Test Cluster"
        assert data["config"] is not None
        assert len(data["plants"]) == 1
        assert len(data["sensors"]) == 1
        assert data["irrigator"] is not None

    def test_status_off_season_reasons_serialize_without_warning(
        self, app: FastAPI, seeded_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: an off-season SEASONAL_* reason must not trip the Pydantic serializer.

        The seasonal rule used to rebind ``decision.reasons`` to a tuple, so
        ``model_dump`` in ``decision_to_view`` emitted
        ``PydanticSerializationUnexpectedValue`` on every status call.
        """
        monkeypatch.setattr("greenhouse_core.logic.engine.season_for", lambda *_a, **_k: "winter")
        # Opt this cluster out of any inherited quiet window so the wall-clock
        # hour cannot short-circuit the pipeline before the seasonal rule.
        resp = seeded_client.put("/api/v1/clusters/1/config", json={"quiet_start_hour": 0, "quiet_end_hour": 0})
        assert resp.status_code == HTTPStatus.OK
        with app.state.session_factory() as session:
            IrrigationRepository(session).add_sensor_reading(sensor_id=1, soil_moisture=55.0)
            session.commit()

        with warnings.catch_warnings():
            warnings.filterwarnings("error", message="Pydantic serializer warnings")
            resp = seeded_client.get("/api/v1/clusters/1/status")

        assert resp.status_code == HTTPStatus.OK, resp.text
        codes = [r["code"] for r in resp.json()["decision"]["reasons"]]
        assert "seasonal_hold" in codes

    @pytest.mark.usefixtures("frozen_clock")
    def test_status_reports_the_real_age_of_a_reading_older_than_a_day(self, client: TestClient) -> None:
        """B-22: a sensor quiet for 3 days shows its last reading and its real age, not ``None``."""
        cid = client.post("/api/v1/clusters", json={"name": "Quiet"}).json()["id"]
        sid = client.post(
            f"/api/v1/clusters/{cid}/sensors",
            json={"tuya_device_id": "fake_tuya_sensor_00000001", "name": "S", "type": "tuya.tr301z"},
        ).json()["id"]
        three_days = 3 * 24 * 3600
        soil = 41.0
        session = app_of(client).state.session_factory()
        repo = IrrigationRepository(session)
        repo.add_sensor_reading(sid, timestamp=FROZEN_TS - three_days, soil_moisture=soil)
        repo.commit()
        session.close()

        [row] = client.get(f"/api/v1/clusters/{cid}/status").json()["sensors"]
        assert row["reading_age_seconds"] == three_days
        assert row["last_reading"]["soil_moisture"] == soil

    def test_status_not_found(self, client: TestClient) -> None:
        """Status not found."""
        resp = client.get("/api/v1/clusters/999/status")
        assert resp.status_code == HTTPStatus.NOT_FOUND


class TestClusterHistory:
    """Tests for cluster history."""

    def test_history_empty(self, seeded_client: TestClient) -> None:
        """History empty."""
        resp = seeded_client.get("/api/v1/clusters/1/history")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["cluster_name"] == "Test Cluster"
        assert isinstance(data["sensors"], list)
        assert isinstance(data["irrigators"], list)

    def test_history_custom_hours(self, seeded_client: TestClient) -> None:
        """History custom hours."""
        resp = seeded_client.get("/api/v1/clusters/1/history?hours=48")
        assert resp.status_code == HTTPStatus.OK

    def test_history_not_found(self, client: TestClient) -> None:
        """History not found."""
        resp = client.get("/api/v1/clusters/999/history")
        assert resp.status_code == HTTPStatus.NOT_FOUND


class TestClusterDetail:
    """Tests for cluster detail."""

    def test_detail_inlines_children(self, seeded_client: TestClient) -> None:
        """GET /clusters/{id}/detail returns plants/sensors/irrigators/config/windows."""
        resp = seeded_client.get("/api/v1/clusters/1/detail")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["cluster"]["name"] == "Test Cluster"
        assert len(data["plants"]) == 1
        assert data["plants"][0]["species"] == "Monstera deliciosa"
        assert len(data["sensors"]) == 1
        assert data["irrigator"] is not None
        assert data["irrigator"]["name"]
        assert data["config"] is not None
        assert data["config"]["mode"] == "smart"
        assert data["windows"] == []

    def test_detail_includes_windows(self, seeded_client: TestClient) -> None:
        """Configured irrigation windows show up in the detail payload."""
        window = {"start_hour": 6, "end_hour": 9, "weekday_mask": 127, "label": "AM"}
        seeded_client.post("/api/v1/clusters/1/windows", json=window)
        resp = seeded_client.get("/api/v1/clusters/1/detail")
        windows = resp.json()["windows"]
        assert len(windows) == 1
        assert windows[0]["start_hour"] == window["start_hour"]

    def test_detail_not_found(self, client: TestClient) -> None:
        """Detail not found."""
        resp = client.get("/api/v1/clusters/999/detail")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_plain_get_unchanged(self, seeded_client: TestClient) -> None:
        """GET /clusters/{id} still returns the flat ClusterResponse shape."""
        resp = seeded_client.get("/api/v1/clusters/1")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        # Flat: top-level id/name, not nested under 'cluster'
        assert "cluster" not in data
        assert data["id"] == 1
        assert data["name"] == "Test Cluster"

    def test_detail_cross_cluster_isolation(self, seeded_client: TestClient) -> None:
        """Detail for cluster A must not leak rows from cluster B."""
        seeded_client.post("/api/v1/clusters", json={"name": "Other"})
        seeded_client.post("/api/v1/clusters/2/plants", json={"species": "Other Plant"})
        resp = seeded_client.get("/api/v1/clusters/1/detail")
        plant_species = [p["species"] for p in resp.json()["plants"]]
        assert "Other Plant" not in plant_species
