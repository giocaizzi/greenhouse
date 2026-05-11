"""Functional tests for cluster CRUD + operations via HTTP."""


class TestClusterCRUD:
    def test_create_and_list(self, client):
        resp = client.post("/api/v1/clusters", json={"name": "My Cluster"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Cluster"
        assert data["environment"] == "indoor"
        assert data["id"] == 1

        resp = client.get("/api/v1/clusters")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_create_with_all_fields(self, client):
        resp = client.post(
            "/api/v1/clusters",
            json={"name": "Outdoor", "location": "Balcony", "environment": "outdoor"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["location"] == "Balcony"
        assert data["environment"] == "outdoor"

    def test_get_by_id(self, client):
        client.post("/api/v1/clusters", json={"name": "Test"})
        resp = client.get("/api/v1/clusters/1")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test"

    def test_get_not_found(self, client):
        resp = client.get("/api/v1/clusters/999")
        assert resp.status_code == 404

    def test_list_empty(self, client):
        resp = client.get("/api/v1/clusters")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_two_clusters(self, client):
        client.post("/api/v1/clusters", json={"name": "A"})
        client.post("/api/v1/clusters", json={"name": "B"})
        resp = client.get("/api/v1/clusters")
        assert len(resp.json()) == 2

    def test_update_name_and_location(self, client):
        client.post("/api/v1/clusters", json={"name": "Original", "environment": "indoor"})
        resp = client.put("/api/v1/clusters/1", json={"name": "Renamed", "location": "Shelf"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Renamed"
        assert data["location"] == "Shelf"
        assert data["environment"] == "indoor"  # unchanged

    def test_update_reflects_on_get(self, client):
        client.post("/api/v1/clusters", json={"name": "Before"})
        client.put("/api/v1/clusters/1", json={"name": "After"})
        resp = client.get("/api/v1/clusters/1")
        assert resp.json()["name"] == "After"

    def test_update_not_found(self, client):
        resp = client.put("/api/v1/clusters/999", json={"name": "X"})
        assert resp.status_code == 404

    def test_delete_cluster(self, client):
        client.post("/api/v1/clusters", json={"name": "ToDelete"})
        resp = client.delete("/api/v1/clusters/1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        resp = client.get("/api/v1/clusters/1")
        assert resp.status_code == 404

    def test_delete_cascades_to_plants(self, client):
        client.post("/api/v1/clusters", json={"name": "C"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        client.delete("/api/v1/clusters/1")
        resp = client.get("/api/v1/clusters/1")
        assert resp.status_code == 404

    def test_delete_not_found(self, client):
        resp = client.delete("/api/v1/clusters/999")
        assert resp.status_code == 404

    def test_double_delete_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "Once"})
        client.delete("/api/v1/clusters/1")
        resp = client.delete("/api/v1/clusters/1")
        assert resp.status_code == 404


class TestClusterStatus:
    def test_status_seeded(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cluster"]["name"] == "Test Cluster"
        assert data["config"] is not None
        assert len(data["plants"]) == 1
        assert len(data["sensors"]) == 1
        assert len(data["irrigators"]) == 1

    def test_status_not_found(self, client):
        resp = client.get("/api/v1/clusters/999/status")
        assert resp.status_code == 404


class TestClusterHistory:
    def test_history_empty(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cluster_name"] == "Test Cluster"
        assert isinstance(data["sensors"], list)
        assert isinstance(data["irrigators"], list)

    def test_history_custom_hours(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/history?hours=48")
        assert resp.status_code == 200

    def test_history_not_found(self, client):
        resp = client.get("/api/v1/clusters/999/history")
        assert resp.status_code == 404
