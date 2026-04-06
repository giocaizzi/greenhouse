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


class TestIrrigatorControl:
    def test_start(self, seeded_client):
        resp = seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 5})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify event logged via history
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
