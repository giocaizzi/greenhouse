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
