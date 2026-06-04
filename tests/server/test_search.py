"""Functional tests for the /api/v1/search endpoint."""


def _seed(client):
    """Seed a cluster, plant, sensor, and irrigator for search tests."""
    client.post("/api/v1/clusters", json={"name": "Living Room", "location": "Window sill"})
    client.post("/api/v1/clusters/1/plants", json={"species": "Monstera deliciosa", "notes": "tropical beauty"})
    client.post(
        "/api/v1/clusters/1/sensors",
        json={"tuya_device_id": "fake_soil_sensor_001", "name": "soil-1", "type": "soil_moisture"},
    )
    client.post(
        "/api/v1/clusters/1/irrigator",
        json={"tuya_device_id": "fake_irrigator_drip_001", "name": "drip-1", "type": "tuya_cloud"},
    )


class TestSearch:
    def test_empty_query_returns_no_hits(self, client):
        _seed(client)
        resp = client.get("/api/v1/search?q=")
        assert resp.status_code == 200
        data = resp.json()
        assert data["hits"] == []
        assert data["query"] == ""

    def test_cluster_match_by_name(self, client):
        _seed(client)
        resp = client.get("/api/v1/search?q=living")
        assert resp.status_code == 200
        hits = resp.json()["hits"]
        cluster_hits = [h for h in hits if h["entity_type"] == "cluster"]
        assert len(cluster_hits) == 1
        assert cluster_hits[0]["label"] == "Living Room"
        assert cluster_hits[0]["href"] == "/clusters/1"
        assert cluster_hits[0]["sublabel"] == "Window sill"

    def test_cluster_match_by_location(self, client):
        _seed(client)
        resp = client.get("/api/v1/search?q=window")
        assert resp.status_code == 200
        hits = resp.json()["hits"]
        cluster_hits = [h for h in hits if h["entity_type"] == "cluster"]
        assert len(cluster_hits) == 1
        assert cluster_hits[0]["label"] == "Living Room"

    def test_plant_match_by_species(self, client):
        _seed(client)
        resp = client.get("/api/v1/search?q=monst")
        assert resp.status_code == 200
        hits = resp.json()["hits"]
        plant_hits = [h for h in hits if h["entity_type"] == "plant"]
        assert len(plant_hits) == 1
        assert plant_hits[0]["label"] == "Monstera deliciosa"
        assert plant_hits[0]["href"] == "/clusters/1/plants/1"
        assert plant_hits[0]["sublabel"] == "Living Room"

    def test_sensor_match_by_name(self, client):
        _seed(client)
        resp = client.get("/api/v1/search?q=soil")
        assert resp.status_code == 200
        hits = resp.json()["hits"]
        sensor_hits = [h for h in hits if h["entity_type"] == "sensor"]
        assert len(sensor_hits) == 1
        assert sensor_hits[0]["label"] == "soil-1"
        assert sensor_hits[0]["href"] == "/clusters/1#sensor-1"
        assert sensor_hits[0]["sublabel"] == "Living Room"

    def test_irrigator_match_by_name(self, client):
        _seed(client)
        resp = client.get("/api/v1/search?q=drip")
        assert resp.status_code == 200
        hits = resp.json()["hits"]
        irr_hits = [h for h in hits if h["entity_type"] == "irrigator"]
        assert len(irr_hits) == 1
        assert irr_hits[0]["label"] == "drip-1"
        assert irr_hits[0]["href"] == "/clusters/1#irrigator-1"

    def test_case_insensitive(self, client):
        _seed(client)
        resp_lower = client.get("/api/v1/search?q=monstera")
        resp_upper = client.get("/api/v1/search?q=MONSTERA")
        hits_lower = [h for h in resp_lower.json()["hits"] if h["entity_type"] == "plant"]
        hits_upper = [h for h in resp_upper.json()["hits"] if h["entity_type"] == "plant"]
        assert len(hits_lower) == len(hits_upper) == 1

    def test_no_match_returns_empty(self, client):
        _seed(client)
        resp = client.get("/api/v1/search?q=xyznonexistent")
        assert resp.status_code == 200
        assert resp.json()["hits"] == []

    def test_limit_respected(self, client):
        """Limit parameter caps results."""
        _seed(client)
        # Add more clusters to exceed limit
        for i in range(10):
            client.post("/api/v1/clusters", json={"name": f"Living Extra {i}"})
        resp = client.get("/api/v1/search?q=living&limit=3")
        assert resp.status_code == 200
        assert len(resp.json()["hits"]) <= 3

    def test_query_echoed_back(self, client):
        _seed(client)
        resp = client.get("/api/v1/search?q=monst")
        assert resp.json()["query"] == "monst"
