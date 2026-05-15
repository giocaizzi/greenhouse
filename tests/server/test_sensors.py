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


class TestListAllSensorsTopLevel:
    def test_list_empty(self, client):
        """GET /sensors with no rows returns empty list and null cursor."""
        resp = client.get("/api/v1/sensors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sensors"] == []
        assert data["next_cursor"] is None

    def test_list_across_clusters(self, client):
        """GET /sensors returns every sensor regardless of cluster."""
        client.post("/api/v1/clusters", json={"name": "A"})
        client.post("/api/v1/clusters", json={"name": "B"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "dev_a", "name": "A1", "type": "soil_moisture"},
        )
        client.post(
            "/api/v1/clusters/2/sensors",
            json={"tuya_device_id": "dev_b", "name": "B1", "type": "soil_moisture"},
        )
        resp = client.get("/api/v1/sensors")
        names = sorted(s["name"] for s in resp.json()["sensors"])
        assert names == ["A1", "B1"]

    def test_filter_by_cluster_id(self, client):
        """cluster_id filter restricts to one cluster."""
        client.post("/api/v1/clusters", json={"name": "A"})
        client.post("/api/v1/clusters", json={"name": "B"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "dev_a", "name": "A1", "type": "soil_moisture"},
        )
        client.post(
            "/api/v1/clusters/2/sensors",
            json={"tuya_device_id": "dev_b", "name": "B1", "type": "soil_moisture"},
        )
        resp = client.get("/api/v1/sensors?cluster_id=2")
        names = [s["name"] for s in resp.json()["sensors"]]
        assert names == ["B1"]

    def test_pagination(self, client):
        """limit + cursor walks the rows id-ascending."""
        client.post("/api/v1/clusters", json={"name": "A"})
        for n in range(3):
            client.post(
                "/api/v1/clusters/1/sensors",
                json={"tuya_device_id": f"dev_{n}", "name": f"S{n}", "type": "soil_moisture"},
            )
        resp = client.get("/api/v1/sensors?limit=2")
        data = resp.json()
        assert len(data["sensors"]) == 2
        assert data["next_cursor"] is not None
        resp = client.get(f"/api/v1/sensors?limit=2&cursor={data['next_cursor']}")
        data2 = resp.json()
        assert len(data2["sensors"]) == 1
        assert data2["next_cursor"] is None


class TestSensorReassignmentActivity:
    def test_put_plant_id_change_emits_sensor_reassigned_event(self, client):
        """Changing a sensor's plant_id via PUT must publish a sensor_reassigned activity event."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Monstera"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "s001", "name": "S1", "type": "soil_moisture", "plant_id": 1},
        )

        resp = client.put("/api/v1/clusters/1/sensors/1", json={"plant_id": 2})
        assert resp.status_code == 200

        resp = client.get("/api/v1/activity?entity_type=sensor&entity_id=1")
        codes = [e["code"] for e in resp.json()["items"]]
        assert "sensor_reassigned" in codes

    def test_no_op_plant_id_change_does_not_emit_event(self, client):
        """Setting plant_id to its current value must NOT publish an event."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "s001", "name": "S1", "type": "soil_moisture", "plant_id": 1},
        )
        # No-op PUT (same plant_id).
        client.put("/api/v1/clusters/1/sensors/1", json={"plant_id": 1})

        resp = client.get("/api/v1/activity?entity_type=sensor&entity_id=1")
        codes = [e["code"] for e in resp.json()["items"]]
        assert "sensor_reassigned" not in codes

    def test_clearing_plant_id_emits_event(self, client):
        """Detaching a sensor (setting plant_id to None) also publishes the event."""
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "s001", "name": "S1", "type": "soil_moisture", "plant_id": 1},
        )
        # We can't send null via the existing PUT (exclude_none drops it), so
        # exercise it directly through the repository for coverage of the path.
        from greenhouse_core.repository import IrrigationRepository

        # Resolve the underlying session factory via TestClient app state.
        app = client.app
        with app.state.session_factory() as session:
            repo = IrrigationRepository(session)
            repo.reassign_sensor_to_plant(1, None)
            session.commit()

        resp = client.get("/api/v1/activity?entity_type=sensor&entity_id=1")
        codes = [e["code"] for e in resp.json()["items"]]
        assert "sensor_reassigned" in codes
