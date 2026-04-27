"""Functional tests for sensor CRUD via HTTP."""


class TestSensorCRUD:
    def test_add_and_list(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "sens001", "name": "Soil Sensor", "type": "soil_moisture"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Soil Sensor"

        resp = client.get("/api/v1/clusters/1/sensors")
        assert len(resp.json()) == 1

    def test_add_with_plant(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        resp = client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "sens001", "name": "S1", "type": "soil_moisture", "plant_id": 1},
        )
        assert resp.status_code == 201
        assert resp.json()["plant_id"] == 1

    def test_add_duplicate_device(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "sens001", "name": "A", "type": "soil_moisture"},
        )
        resp = client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "sens001", "name": "B", "type": "soil_moisture"},
        )
        assert resp.status_code == 409

    def test_list_empty(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.get("/api/v1/clusters/1/sensors")
        assert resp.json() == []

    def test_get_by_id(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "sens001", "name": "My Sensor", "type": "soil_moisture"},
        )
        resp = client.get("/api/v1/clusters/1/sensors/1")
        assert resp.status_code == 200
        assert resp.json()["name"] == "My Sensor"

    def test_get_wrong_cluster_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters", json={"name": "C2"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "sens001", "name": "S1", "type": "soil_moisture"},
        )
        resp = client.get("/api/v1/clusters/2/sensors/1")
        assert resp.status_code == 404

    def test_get_not_found(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.get("/api/v1/clusters/1/sensors/999")
        assert resp.status_code == 404

    def test_update_name_and_plant_id(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "sens001", "name": "Old Name", "type": "soil_moisture"},
        )
        resp = client.put(
            "/api/v1/clusters/1/sensors/1",
            json={"name": "New Name", "plant_id": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "New Name"
        assert data["plant_id"] == 1

    def test_update_wrong_cluster_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters", json={"name": "C2"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "sens001", "name": "S", "type": "soil_moisture"},
        )
        resp = client.put("/api/v1/clusters/2/sensors/1", json={"name": "X"})
        assert resp.status_code == 404

    def test_update_not_found(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.put("/api/v1/clusters/1/sensors/999", json={"name": "X"})
        assert resp.status_code == 404

    def test_delete_sensor(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "sens001", "name": "S1", "type": "soil_moisture"},
        )
        resp = client.delete("/api/v1/clusters/1/sensors/1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        resp = client.get("/api/v1/clusters/1/sensors/1")
        assert resp.status_code == 404

    def test_delete_wrong_cluster_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters", json={"name": "C2"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "sens001", "name": "S", "type": "soil_moisture"},
        )
        resp = client.delete("/api/v1/clusters/2/sensors/1")
        assert resp.status_code == 404

    def test_delete_not_found(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.delete("/api/v1/clusters/1/sensors/999")
        assert resp.status_code == 404
