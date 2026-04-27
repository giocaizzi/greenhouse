"""Functional tests for irrigator CRUD + control via HTTP."""


class TestIrrigatorCRUD:
    def test_add_and_list(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Pump"

        resp = client.get("/api/v1/clusters/1/irrigators")
        assert len(resp.json()) == 1

    def test_add_duplicate_device(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "dev001", "name": "A", "type": "tuya_cloud"},
        )
        resp = client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "dev001", "name": "B", "type": "tuya_cloud"},
        )
        assert resp.status_code == 409

    def test_get_by_id(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        resp = client.get("/api/v1/clusters/1/irrigators/1")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Pump"

    def test_get_wrong_cluster_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters", json={"name": "C2"})
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        resp = client.get("/api/v1/clusters/2/irrigators/1")
        assert resp.status_code == 404

    def test_get_not_found(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.get("/api/v1/clusters/1/irrigators/999")
        assert resp.status_code == 404

    def test_update_name_and_config(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "dev001", "name": "Old Pump", "type": "tuya_cloud"},
        )
        resp = client.put(
            "/api/v1/clusters/1/irrigators/1",
            json={"name": "New Pump", "config": {"key": "value"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "New Pump"
        assert data["config"] == {"key": "value"}

    def test_update_wrong_cluster_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters", json={"name": "C2"})
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        resp = client.put("/api/v1/clusters/2/irrigators/1", json={"name": "X"})
        assert resp.status_code == 404

    def test_update_not_found(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.put("/api/v1/clusters/1/irrigators/999", json={"name": "X"})
        assert resp.status_code == 404

    def test_delete_irrigator(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        resp = client.delete("/api/v1/clusters/1/irrigators/1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        resp = client.get("/api/v1/clusters/1/irrigators/1")
        assert resp.status_code == 404

    def test_delete_wrong_cluster_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters", json={"name": "C2"})
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        resp = client.delete("/api/v1/clusters/2/irrigators/1")
        assert resp.status_code == 404

    def test_delete_not_found(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.delete("/api/v1/clusters/1/irrigators/999")
        assert resp.status_code == 404

    def test_double_delete_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "dev001", "name": "Pump", "type": "tuya_cloud"},
        )
        client.delete("/api/v1/clusters/1/irrigators/1")
        resp = client.delete("/api/v1/clusters/1/irrigators/1")
        assert resp.status_code == 404


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
