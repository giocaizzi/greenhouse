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
