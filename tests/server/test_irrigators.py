"""Functional tests for the singular cluster-keyed irrigator CRUD + control via HTTP.

A cluster has at most one irrigator (strict 0:1). CRUD is keyed by cluster
(``/clusters/{id}/irrigator``); device-action endpoints stay keyed by
irrigator id (``/irrigators/{id}/start`` etc.).
"""


class TestIrrigatorCRUD:
    def test_add_and_get(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Pump"

        resp = client.get("/api/v1/clusters/1/irrigator")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Pump"

    def test_add_second_irrigator_conflicts(self, client):
        """A cluster may have at most one irrigator — a second POST returns 409."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        first = client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "A", "type": "tuya_cloud"},
        )
        assert first.status_code == 201
        resp = client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev002", "name": "B", "type": "tuya_cloud"},
        )
        assert resp.status_code == 409

    def test_add_duplicate_device(self, client):
        """Re-using a Tuya device id (here, on another cluster) returns 409."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters", json={"name": "C2"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "A", "type": "tuya_cloud"},
        )
        resp = client.post(
            "/api/v1/clusters/2/irrigator",
            json={"tuya_device_id": "dev001", "name": "B", "type": "tuya_cloud"},
        )
        assert resp.status_code == 409

    def test_get_no_irrigator_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.get("/api/v1/clusters/1/irrigator")
        assert resp.status_code == 404

    def test_update_name_and_config(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Old Pump", "type": "tuya_cloud"},
        )
        resp = client.put(
            "/api/v1/clusters/1/irrigator",
            json={"name": "New Pump", "config": {"key": "value"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "New Pump"
        assert data["config"] == {"key": "value"}

    def test_update_no_irrigator_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.put("/api/v1/clusters/1/irrigator", json={"name": "X"})
        assert resp.status_code == 404

    def test_create_with_capacity_round_trips(self, client):
        """POST with reservoir_l/flow_rate_l_per_min persists and is returned."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.post(
            "/api/v1/clusters/1/irrigator",
            json={
                "tuya_device_id": "dev001",
                "name": "Pump",
                "type": "tuya_cloud",
                "reservoir_l": 12.5,
                "flow_rate_l_per_min": 1.5,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["reservoir_l"] == 12.5
        assert data["flow_rate_l_per_min"] == 1.5

        # Persisted: a fresh GET returns the same values.
        got = client.get("/api/v1/clusters/1/irrigator").json()
        assert got["reservoir_l"] == 12.5
        assert got["flow_rate_l_per_min"] == 1.5

    def test_create_without_capacity_defaults_none(self, client):
        """Capacity fields are optional and default to None."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        data = resp.json()
        assert data["reservoir_l"] is None
        assert data["flow_rate_l_per_min"] is None

    def test_update_capacity_round_trips(self, client):
        """PUT with capacity fields updates and returns the new values."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        resp = client.put(
            "/api/v1/clusters/1/irrigator",
            json={"reservoir_l": 20.0, "flow_rate_l_per_min": 2.0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reservoir_l"] == 20.0
        assert data["flow_rate_l_per_min"] == 2.0

        got = client.get("/api/v1/clusters/1/irrigator").json()
        assert got["reservoir_l"] == 20.0
        assert got["flow_rate_l_per_min"] == 2.0

    def test_update_rejects_negative_capacity(self, client):
        """Negative capacity values are rejected by schema validation (ge=0)."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        resp = client.put("/api/v1/clusters/1/irrigator", json={"reservoir_l": -1.0})
        assert resp.status_code == 422

    def test_delete_irrigator(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        resp = client.delete("/api/v1/clusters/1/irrigator")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        resp = client.get("/api/v1/clusters/1/irrigator")
        assert resp.status_code == 404

    def test_delete_no_irrigator_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.delete("/api/v1/clusters/1/irrigator")
        assert resp.status_code == 404

    def test_double_delete_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        client.delete("/api/v1/clusters/1/irrigator")
        resp = client.delete("/api/v1/clusters/1/irrigator")
        assert resp.status_code == 404

    def test_can_recreate_after_delete(self, client):
        """Deleting frees the 0:1 slot so a new irrigator can be added."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        client.delete("/api/v1/clusters/1/irrigator")
        resp = client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev002", "name": "Pump2", "type": "tuya_cloud"},
        )
        assert resp.status_code == 201


class TestIrrigatorControl:
    def test_start(self, seeded_client):
        resp = seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 5})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        resp = seeded_client.get("/api/v1/clusters/1/history")
        events = resp.json()["irrigators"][0]["events"]
        assert any(e["action"] == "start" for e in events)

    def test_stop(self, seeded_client):
        resp = seeded_client.post("/api/v1/irrigators/1/stop")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_log_manual(self, seeded_client):
        resp = seeded_client.post("/api/v1/irrigators/1/log-manual", json={"minutes": 3})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_log_manual_requires_minutes(self, seeded_client):
        resp = seeded_client.post("/api/v1/irrigators/1/log-manual", json={})
        assert resp.status_code == 422


class TestListAllIrrigatorsTopLevel:
    def test_list_empty(self, client):
        """GET /irrigators with no rows returns empty list and null cursor."""
        resp = client.get("/api/v1/irrigators")
        assert resp.status_code == 200
        data = resp.json()
        assert data["irrigators"] == []
        assert data["next_cursor"] is None

    def test_list_across_clusters(self, client):
        """GET /irrigators returns every irrigator regardless of cluster."""
        client.post("/api/v1/clusters", json={"name": "A"})
        client.post("/api/v1/clusters", json={"name": "B"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "dev_a", "name": "A1", "type": "tuya_cloud"},
        )
        client.post(
            "/api/v1/clusters/2/irrigator",
            json={"tuya_device_id": "dev_b", "name": "B1", "type": "tuya_cloud"},
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
            json={"tuya_device_id": "dev_a", "name": "A1", "type": "tuya_cloud"},
        )
        client.post(
            "/api/v1/clusters/2/irrigator",
            json={"tuya_device_id": "dev_b", "name": "B1", "type": "tuya_cloud"},
        )
        resp = client.get("/api/v1/irrigators?cluster_id=2")
        names = [i["name"] for i in resp.json()["irrigators"]]
        assert names == ["B1"]

    def test_pagination(self, client):
        """limit + cursor walks the rows id-ascending across clusters."""
        for n in range(3):
            client.post("/api/v1/clusters", json={"name": f"C{n}"})
            client.post(
                f"/api/v1/clusters/{n + 1}/irrigator",
                json={"tuya_device_id": f"dev_{n}", "name": f"I{n}", "type": "tuya_cloud"},
            )
        resp = client.get("/api/v1/irrigators?limit=2")
        data = resp.json()
        assert len(data["irrigators"]) == 2
        assert data["next_cursor"] is not None
        resp = client.get(f"/api/v1/irrigators?limit=2&cursor={data['next_cursor']}")
        data2 = resp.json()
        assert len(data2["irrigators"]) == 1
        assert data2["next_cursor"] is None

    def test_get_not_found_on_unknown_cluster(self, client):
        """Filter by an unknown cluster returns an empty page, not a 404."""
        resp = client.get("/api/v1/irrigators?cluster_id=999")
        assert resp.status_code == 200
        assert resp.json()["irrigators"] == []
