"""Functional tests for the singular cluster-keyed irrigator CRUD + control via HTTP.

A cluster has at most one irrigator (strict 0:1). CRUD is keyed by cluster
(``/clusters/{id}/irrigator``); device-action endpoints stay keyed by
irrigator id (``/irrigators/{id}/start`` etc.).
"""

from http import HTTPStatus

from fake_data import FAKE_LOCAL_KEY
from greenhouse_core.models import parse_device_config
from greenhouse_core.repository import IrrigationRepository


class TestIrrigatorCRUD:
    def test_add_and_get(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "rainpoint.ik10pw"},
        )
        assert resp.status_code == HTTPStatus.CREATED
        assert resp.json()["name"] == "Pump"

        resp = client.get("/api/v1/clusters/1/irrigator")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["name"] == "Pump"

    def test_add_second_irrigator_conflicts(self, client):
        """A cluster may have at most one irrigator — a second POST returns 409."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        first = client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "A", "type": "rainpoint.ik10pw"},
        )
        assert first.status_code == HTTPStatus.CREATED
        resp = client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev002", "name": "B", "type": "rainpoint.ik10pw"},
        )
        assert resp.status_code == HTTPStatus.CONFLICT

    def test_add_duplicate_device(self, client):
        """Re-using a Tuya device id (here, on another cluster) returns 409."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters", json={"name": "C2"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "A", "type": "rainpoint.ik10pw"},
        )
        resp = client.post(
            "/api/v1/clusters/2/irrigator",
            json={"tuya_device_id": "dev001", "name": "B", "type": "rainpoint.ik10pw"},
        )
        assert resp.status_code == HTTPStatus.CONFLICT

    def test_get_no_irrigator_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.get("/api/v1/clusters/1/irrigator")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_update_name_and_config(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Old Pump", "type": "rainpoint.ik10pw"},
        )
        resp = client.put(
            "/api/v1/clusters/1/irrigator",
            json={"name": "New Pump", "config": {"key": "value"}},
        )
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["name"] == "New Pump"
        assert data["config"] == {"key": "value"}

    def test_update_no_irrigator_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.put("/api/v1/clusters/1/irrigator", json={"name": "X"})
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_create_with_capacity_round_trips(self, client):
        """POST with reservoir_l/flow_rate_l_per_min persists and is returned."""
        capacity = {"reservoir_l": 12.5, "flow_rate_l_per_min": 1.5}
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "rainpoint.ik10pw", **capacity},
        )
        assert resp.status_code == HTTPStatus.CREATED
        data = resp.json()
        assert {key: data[key] for key in capacity} == capacity

        # Persisted: a fresh GET returns the same values.
        got = client.get("/api/v1/clusters/1/irrigator").json()
        assert {key: got[key] for key in capacity} == capacity

    def test_create_without_capacity_defaults_none(self, client):
        """Capacity fields are optional and default to None."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "rainpoint.ik10pw"},
        )
        data = resp.json()
        assert data["reservoir_l"] is None
        assert data["flow_rate_l_per_min"] is None

    def test_update_capacity_round_trips(self, client):
        """PUT with capacity fields updates and returns the new values."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "rainpoint.ik10pw"},
        )
        capacity = {"reservoir_l": 20.0, "flow_rate_l_per_min": 2.0}
        resp = client.put("/api/v1/clusters/1/irrigator", json=capacity)
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert {key: data[key] for key in capacity} == capacity

        got = client.get("/api/v1/clusters/1/irrigator").json()
        assert {key: got[key] for key in capacity} == capacity

    def test_update_rejects_negative_capacity(self, client):
        """Negative capacity values are rejected by schema validation (ge=0)."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "rainpoint.ik10pw"},
        )
        resp = client.put("/api/v1/clusters/1/irrigator", json={"reservoir_l": -1.0})
        assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_delete_irrigator(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "rainpoint.ik10pw"},
        )
        resp = client.delete("/api/v1/clusters/1/irrigator")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["success"] is True

        resp = client.get("/api/v1/clusters/1/irrigator")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_delete_no_irrigator_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.delete("/api/v1/clusters/1/irrigator")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_double_delete_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "rainpoint.ik10pw"},
        )
        client.delete("/api/v1/clusters/1/irrigator")
        resp = client.delete("/api/v1/clusters/1/irrigator")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_can_recreate_after_delete(self, client):
        """Deleting frees the 0:1 slot so a new irrigator can be added."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "rainpoint.ik10pw"},
        )
        client.delete("/api/v1/clusters/1/irrigator")
        resp = client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev002", "name": "Pump2", "type": "rainpoint.ik10pw"},
        )
        assert resp.status_code == HTTPStatus.CREATED


class TestIrrigatorControl:
    def test_start(self, seeded_client):
        resp = seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 5})
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["success"] is True

        resp = seeded_client.get("/api/v1/clusters/1/history")
        events = resp.json()["irrigators"][0]["events"]
        assert any(e["action"] == "start" for e in events)

    def test_stop(self, seeded_client):
        resp = seeded_client.post("/api/v1/irrigators/1/stop")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["success"] is True

    def test_log_manual(self, seeded_client):
        resp = seeded_client.post("/api/v1/irrigators/1/log-manual", json={"minutes": 3})
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["success"] is True

    def test_log_manual_requires_minutes(self, seeded_client):
        resp = seeded_client.post("/api/v1/irrigators/1/log-manual", json={})
        assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


class TestListAllIrrigatorsTopLevel:
    def test_list_empty(self, client):
        """GET /irrigators with no rows returns empty list and null cursor."""
        resp = client.get("/api/v1/irrigators")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["irrigators"] == []
        assert data["next_cursor"] is None

    def test_list_across_clusters(self, client):
        """GET /irrigators returns every irrigator regardless of cluster."""
        client.post("/api/v1/clusters", json={"name": "A"})
        client.post("/api/v1/clusters", json={"name": "B"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev_a", "name": "A1", "type": "rainpoint.ik10pw"},
        )
        client.post(
            "/api/v1/clusters/2/irrigator",
            json={"tuya_device_id": "dev_b", "name": "B1", "type": "rainpoint.ik10pw"},
        )
        resp = client.get("/api/v1/irrigators")
        names = sorted(i["name"] for i in resp.json()["irrigators"])
        assert names == ["A1", "B1"]

    def test_filter_by_cluster_id(self, client):
        """cluster_id filter restricts to one cluster."""
        client.post("/api/v1/clusters", json={"name": "A"})
        client.post("/api/v1/clusters", json={"name": "B"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev_a", "name": "A1", "type": "rainpoint.ik10pw"},
        )
        client.post(
            "/api/v1/clusters/2/irrigator",
            json={"tuya_device_id": "dev_b", "name": "B1", "type": "rainpoint.ik10pw"},
        )
        resp = client.get("/api/v1/irrigators?cluster_id=2")
        names = [i["name"] for i in resp.json()["irrigators"]]
        assert names == ["B1"]

    def test_pagination(self, client):
        """Limit + cursor walks the rows id-ascending across clusters."""
        for n in range(3):
            client.post("/api/v1/clusters", json={"name": f"C{n}"})
            client.post(
                f"/api/v1/clusters/{n + 1}/irrigator",
                json={"tuya_device_id": f"dev_{n}", "name": f"I{n}", "type": "rainpoint.ik10pw"},
            )
        page_size = 2
        resp = client.get(f"/api/v1/irrigators?limit={page_size}")
        data = resp.json()
        assert len(data["irrigators"]) == page_size
        assert data["next_cursor"] is not None
        resp = client.get(f"/api/v1/irrigators?limit={page_size}&cursor={data['next_cursor']}")
        data2 = resp.json()
        assert len(data2["irrigators"]) == 1
        assert data2["next_cursor"] is None

    def test_get_not_found_on_unknown_cluster(self, client):
        """Filter by an unknown cluster returns an empty page, not a 404."""
        resp = client.get("/api/v1/irrigators?cluster_id=999")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["irrigators"] == []


class TestLocalKeyMasking:
    """B-11: the API never returns ``local_key`` in plain text; writes still accept it."""

    SECRET = FAKE_LOCAL_KEY
    MASK = "••••••••"

    def _seed(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        return client.post(
            "/api/v1/clusters/1/irrigator",
            json={
                "tuya_device_id": "fake_tuya_device_aabbccdd",
                "name": "Pump",
                "type": "rainpoint.ik10pw",
                "config": {"device_ip": "192.0.2.10", "local_key": self.SECRET},
            },
        )

    @staticmethod
    def _stored_config(client):
        session = client.app.state.session_factory()
        try:
            return parse_device_config(IrrigationRepository(session).get_irrigator_for_cluster(1).config)
        finally:
            session.close()

    def test_every_irrigator_response_masks_the_local_key(self, client):
        created = self._seed(client)
        expected = {"device_ip": "192.0.2.10", "local_key": self.MASK}
        assert created.json()["config"] == expected
        assert client.get("/api/v1/clusters/1/irrigator").json()["config"] == expected
        assert client.get("/api/v1/irrigators").json()["irrigators"][0]["config"] == expected
        assert client.get("/api/v1/clusters/1/detail").json()["irrigator"]["config"] == expected
        updated = client.put("/api/v1/clusters/1/irrigator", json={"name": "Renamed"})
        assert updated.json()["config"] == expected
        assert self.SECRET not in client.get("/api/v1/clusters/1/irrigator").text
        assert self._stored_config(client)["local_key"] == self.SECRET

    def test_sending_the_mask_back_keeps_the_stored_key(self, client):
        self._seed(client)
        round_trip = client.get("/api/v1/clusters/1/irrigator").json()["config"]
        round_trip["device_ip"] = "192.0.2.11"
        resp = client.put("/api/v1/clusters/1/irrigator", json={"config": round_trip})
        assert resp.status_code == HTTPStatus.OK
        assert self._stored_config(client) == {"device_ip": "192.0.2.11", "local_key": self.SECRET}

    def test_a_new_local_key_replaces_the_stored_one(self, client):
        self._seed(client)
        resp = client.put(
            "/api/v1/clusters/1/irrigator",
            json={"config": {"device_ip": "192.0.2.10", "local_key": "fake_local_key_rotated"}},
        )
        assert resp.json()["config"]["local_key"] == self.MASK
        assert self._stored_config(client)["local_key"] == "fake_local_key_rotated"

    def test_no_local_key_means_nothing_to_mask(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "d1", "name": "P", "type": "rainpoint.ik10pw", "config": {"local_key": ""}},
        )
        assert client.get("/api/v1/clusters/1/irrigator").json()["config"] == {"local_key": ""}
