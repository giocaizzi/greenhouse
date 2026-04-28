"""Web data-quality page tests: renders report and shows issue codes."""


class TestQualityPage:
    def test_renders_ok(self, client):
        resp = client.get("/quality")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Data Quality" in resp.text

    def test_empty_state(self, client):
        """Empty DB → all-clear state."""
        resp = client.get("/quality")
        assert resp.status_code == 200
        assert "No issues found" in resp.text

    def test_shows_issue_codes(self, app):
        """Seeding a sensor without a plant and a cluster without config → codes appear."""
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            # Cluster with no irrigation config
            client.post("/api/v1/clusters", json={"name": "QC Cluster"})
            # Sensor not assigned to any plant → sensor_without_plant issue
            client.post(
                "/api/v1/clusters/1/sensors",
                json={"tuya_device_id": "fake_qc_s1", "name": "Unassigned Sensor", "type": "soil_moisture"},
            )

            resp = client.get("/quality")
            assert resp.status_code == 200
            assert "sensor_without_plant" in resp.text
            assert "cluster_without_config" in resp.text

    def test_severity_badges_present(self, app):
        """Issue rows include a severity badge."""
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            client.post("/api/v1/clusters", json={"name": "Badge Cluster"})
            client.post(
                "/api/v1/clusters/1/sensors",
                json={"tuya_device_id": "fake_badge_s1", "name": "Badge Sensor", "type": "soil_moisture"},
            )

            resp = client.get("/quality")
            assert resp.status_code == 200
            assert "warning" in resp.text
