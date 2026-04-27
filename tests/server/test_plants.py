"""Functional tests for plant CRUD via HTTP."""


class TestPlantCRUD:
    def test_add_and_list(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.post(
            "/api/v1/clusters/1/plants",
            json={"species": "Monstera deliciosa", "category": "tropical", "water_needs": "medium"},
        )
        assert resp.status_code == 201
        assert resp.json()["species"] == "Monstera deliciosa"

        resp = client.get("/api/v1/clusters/1/plants")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_add_to_nonexistent_cluster(self, client):
        resp = client.post("/api/v1/clusters/999/plants", json={"species": "Test"})
        assert resp.status_code == 404

    def test_list_empty_cluster(self, client):
        client.post("/api/v1/clusters", json={"name": "Empty"})
        resp = client.get("/api/v1/clusters/1/plants")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_sync_plants(self, seeded_client):
        resp = seeded_client.post("/api/v1/plants/sync", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["synced"] >= 1
        assert isinstance(data["errors"], list)

    def test_update_species_and_water_needs(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post(
            "/api/v1/clusters/1/plants",
            json={"species": "Fern", "water_needs": "low"},
        )
        resp = client.put(
            "/api/v1/clusters/1/plants/1",
            json={"species": "Boston Fern", "water_needs": "medium"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["species"] == "Boston Fern"
        assert data["water_needs"] == "medium"

    def test_update_wrong_cluster_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters", json={"name": "C2"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        resp = client.put("/api/v1/clusters/2/plants/1", json={"species": "X"})
        assert resp.status_code == 404

    def test_update_not_found(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.put("/api/v1/clusters/1/plants/999", json={"species": "X"})
        assert resp.status_code == 404

    def test_delete_detaches_sensor(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "s001", "name": "S1", "type": "soil_moisture", "plant_id": 1},
        )
        resp = client.get("/api/v1/clusters/1/sensors/1")
        assert resp.json()["plant_id"] == 1

        resp = client.delete("/api/v1/clusters/1/plants/1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        resp = client.get("/api/v1/clusters/1/sensors/1")
        assert resp.status_code == 200
        assert resp.json()["plant_id"] is None

    def test_delete_wrong_cluster_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters", json={"name": "C2"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        resp = client.delete("/api/v1/clusters/2/plants/1")
        assert resp.status_code == 404

    def test_delete_not_found(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        resp = client.delete("/api/v1/clusters/1/plants/999")
        assert resp.status_code == 404
