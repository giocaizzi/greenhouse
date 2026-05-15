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


class TestPlantMove:
    """POST /api/v1/plants/{plant_id}/move — relocate a plant between clusters."""

    def _seed_two_clusters_one_plant(self, client):
        client.post("/api/v1/clusters", json={"name": "Source"})
        client.post("/api/v1/clusters", json={"name": "Target"})
        resp = client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_move_happy_path(self, client):
        plant_id = self._seed_two_clusters_one_plant(client)

        resp = client.post(f"/api/v1/plants/{plant_id}/move", json={"target_cluster_id": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == plant_id
        assert data["cluster_id"] == 2

        # Plant now appears under the target cluster, not the source.
        assert [p["id"] for p in client.get("/api/v1/clusters/2/plants").json()] == [plant_id]
        assert client.get("/api/v1/clusters/1/plants").json() == []

    def test_move_missing_plant_returns_404(self, client):
        client.post("/api/v1/clusters", json={"name": "C1"})
        client.post("/api/v1/clusters", json={"name": "C2"})
        resp = client.post("/api/v1/plants/999/move", json={"target_cluster_id": 2})
        assert resp.status_code == 404
        assert "plant" in resp.json()["detail"].lower()

    def test_move_missing_target_cluster_returns_404(self, client):
        plant_id = self._seed_two_clusters_one_plant(client)
        resp = client.post(f"/api/v1/plants/{plant_id}/move", json={"target_cluster_id": 999})
        assert resp.status_code == 404
        assert "cluster" in resp.json()["detail"].lower()

    def test_move_same_cluster_returns_400(self, client):
        plant_id = self._seed_two_clusters_one_plant(client)
        resp = client.post(f"/api/v1/plants/{plant_id}/move", json={"target_cluster_id": 1})
        assert resp.status_code == 400

    def test_move_writes_plant_moved_activity_event(self, app, client):
        plant_id = self._seed_two_clusters_one_plant(client)
        client.post(f"/api/v1/plants/{plant_id}/move", json={"target_cluster_id": 2})

        # The activity timeline must surface the move.
        events = client.get("/api/v1/activity", params={"entity_type": "plant"}).json()
        moved = [e for e in events["items"] if e["code"] == "plant_moved"]
        assert len(moved) == 1, f"expected one plant_moved event, got: {events}"
        assert moved[0]["entity_id"] == plant_id
        assert moved[0]["entity_type"] == "plant"

        # And the persisted row must carry the typed from→to payload.
        import json as _json

        with app.state.session_factory() as session:
            from greenhouse_core.repository import IrrigationRepository

            repo = IrrigationRepository(session)
            rows = repo.list_activity_events(entity_type="plant", entity_id=plant_id)
            moved_rows = [r for r in rows if r.code == "plant_moved"]
            assert len(moved_rows) == 1
            assert _json.loads(moved_rows[0].payload_json) == {
                "from_cluster_id": 1,
                "to_cluster_id": 2,
                "sensor_ids": [],
            }

    def test_move_reassigns_plant_sensors_to_target_cluster(self, client):
        """A sensor attached to the plant is a probe in its soil — it must
        physically follow the plant to the new cluster, otherwise its
        historical readings vanish from the new cluster's charts and keep
        showing up under the old one."""
        plant_id = self._seed_two_clusters_one_plant(client)

        # Attach a sensor to the plant on the source cluster (id=1).
        client.post(
            "/api/v1/clusters/1/sensors",
            json={
                "tuya_device_id": "fake_tuya_device_aabbccdd",
                "name": "Soil probe",
                "type": "soil_moisture",
                "plant_id": plant_id,
            },
        )
        # Also a free sensor that is NOT linked to the plant — must stay put.
        client.post(
            "/api/v1/clusters/1/sensors",
            json={
                "tuya_device_id": "fake_tuya_device_eeff0011",
                "name": "Ambient",
                "type": "environment",
            },
        )

        resp = client.post(f"/api/v1/plants/{plant_id}/move", json={"target_cluster_id": 2})
        assert resp.status_code == 200

        # The plant-linked sensor now belongs to cluster 2; the free sensor stays on 1.
        target_sensors = client.get("/api/v1/clusters/2/sensors").json()
        assert [s["tuya_device_id"] for s in target_sensors] == ["fake_tuya_device_aabbccdd"]
        assert [s["plant_id"] for s in target_sensors] == [plant_id]

        source_sensors = client.get("/api/v1/clusters/1/sensors").json()
        assert [s["tuya_device_id"] for s in source_sensors] == ["fake_tuya_device_eeff0011"]

    def test_move_sensor_readings_follow_via_sensor(self, app, client):
        """SensorReading rows key off sensor_id, so once the sensor moves with
        the plant the historical readings surface under the new cluster."""
        plant_id = self._seed_two_clusters_one_plant(client)
        client.post(
            "/api/v1/clusters/1/sensors",
            json={
                "tuya_device_id": "fake_tuya_device_aabbccdd",
                "name": "Soil probe",
                "type": "soil_moisture",
                "plant_id": plant_id,
            },
        )

        with app.state.session_factory() as session:
            from greenhouse_core.repository import IrrigationRepository

            repo = IrrigationRepository(session)
            repo.add_sensor_reading(sensor_id=1, timestamp=1700000000, soil_moisture=42.0)
            session.commit()

        resp = client.post(f"/api/v1/plants/{plant_id}/move", json={"target_cluster_id": 2})
        assert resp.status_code == 200

        # Same sensor, same readings — now reachable via the new cluster.
        with app.state.session_factory() as session:
            from greenhouse_core.repository import IrrigationRepository

            repo = IrrigationRepository(session)
            assert [s.id for s in repo.get_sensors_in_cluster(2)] == [1]
            assert repo.get_sensors_in_cluster(1) == []

    def test_move_activity_payload_lists_moved_sensor_ids(self, app, client):
        """The plant_moved activity payload must list which sensors travelled."""
        plant_id = self._seed_two_clusters_one_plant(client)
        client.post(
            "/api/v1/clusters/1/sensors",
            json={
                "tuya_device_id": "fake_tuya_device_aabbccdd",
                "name": "Soil probe",
                "type": "soil_moisture",
                "plant_id": plant_id,
            },
        )

        client.post(f"/api/v1/plants/{plant_id}/move", json={"target_cluster_id": 2})

        import json as _json

        with app.state.session_factory() as session:
            from greenhouse_core.repository import IrrigationRepository

            repo = IrrigationRepository(session)
            rows = repo.list_activity_events(entity_type="plant", entity_id=plant_id)
            moved_rows = [r for r in rows if r.code == "plant_moved"]
            assert _json.loads(moved_rows[0].payload_json) == {
                "from_cluster_id": 1,
                "to_cluster_id": 2,
                "sensor_ids": [1],
            }

    def test_move_preserves_decision_logs_on_old_cluster(self, app, client):
        """Decision logs written BEFORE the move stay attached to the original cluster."""
        plant_id = self._seed_two_clusters_one_plant(client)

        # Manually seed a decision log against the source cluster, BEFORE the move.
        with app.state.session_factory() as session:
            from greenhouse_core.repository import IrrigationRepository

            repo = IrrigationRepository(session)
            repo.add_decision_log(
                cluster_id=1,
                evaluated_at=1700000000,
                action="skip",
                duration_minutes=0,
                interval_hours=6,
                confidence=0.5,
                primary_code="ADEQUATE_MOISTURE",
                reason_text="adequate moisture",
                payload={"sensor_data": {}},
            )
            session.commit()

        resp = client.post(f"/api/v1/plants/{plant_id}/move", json={"target_cluster_id": 2})
        assert resp.status_code == 200

        # The decision log must still belong to cluster 1, not cluster 2.
        with app.state.session_factory() as session:
            from greenhouse_core.repository import IrrigationRepository

            repo = IrrigationRepository(session)
            assert len(repo.list_decision_logs(cluster_id=1)) == 1
            assert repo.list_decision_logs(cluster_id=2) == []

    def test_move_preserves_irrigation_events_on_old_cluster(self, app, client):
        """Irrigation events written BEFORE the move stay attached to irrigators
        on the original cluster (audit integrity)."""
        plant_id = self._seed_two_clusters_one_plant(client)
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "fake_tuya_irrigator_aabb", "name": "Source Irrigator", "type": "tuya_cloud"},
        )

        with app.state.session_factory() as session:
            from greenhouse_core.repository import IrrigationRepository

            repo = IrrigationRepository(session)
            repo.add_irrigation_event(
                irrigator_id=1,
                action="start",
                duration_minutes=5,
                triggered_by="auto",
                notes="pre-move event",
            )
            session.commit()

        resp = client.post(f"/api/v1/plants/{plant_id}/move", json={"target_cluster_id": 2})
        assert resp.status_code == 200

        with app.state.session_factory() as session:
            from greenhouse_core.repository import IrrigationRepository

            repo = IrrigationRepository(session)
            events = repo.get_recent_events(irrigator_id=1, hours=24 * 365)
            assert len(events) == 1
            assert events[0].irrigator.cluster_id == 1  # still on the source cluster

    def test_move_preserves_plant_health_history(self, app, client):
        """plant_health_daily snapshots are keyed by plant_id and must still
        query cleanly after a move."""
        plant_id = self._seed_two_clusters_one_plant(client)

        with app.state.session_factory() as session:
            from greenhouse_core.repository import IrrigationRepository

            repo = IrrigationRepository(session)
            repo.upsert_plant_health(
                plant_id=plant_id,
                date_key="2024-01-01",
                score=72.5,
                soil_in_band_pct=0.8,
                temp_in_band_pct=0.9,
                humidity_in_band_pct=0.7,
                efficiency=0.6,
            )
            session.commit()

        resp = client.post(f"/api/v1/plants/{plant_id}/move", json={"target_cluster_id": 2})
        assert resp.status_code == 200

        with app.state.session_factory() as session:
            from greenhouse_core.repository import IrrigationRepository

            repo = IrrigationRepository(session)
            history = repo.list_plant_health_history(plant_id, days=365 * 100)
            assert len(history) == 1
            assert history[0].score == 72.5


class TestListAllPlantsTopLevel:
    def test_list_empty(self, client):
        """GET /plants with no plants returns empty list and null cursor."""
        resp = client.get("/api/v1/plants")
        assert resp.status_code == 200
        data = resp.json()
        assert data["plants"] == []
        assert data["next_cursor"] is None

    def test_list_across_clusters(self, client):
        """GET /plants returns every plant regardless of cluster."""
        client.post("/api/v1/clusters", json={"name": "A"})
        client.post("/api/v1/clusters", json={"name": "B"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        client.post("/api/v1/clusters/2/plants", json={"species": "Monstera"})
        resp = client.get("/api/v1/plants")
        assert resp.status_code == 200
        species = sorted(p["species"] for p in resp.json()["plants"])
        assert species == ["Fern", "Monstera"]

    def test_filter_by_cluster_id(self, client):
        """cluster_id query param restricts results to one cluster."""
        client.post("/api/v1/clusters", json={"name": "A"})
        client.post("/api/v1/clusters", json={"name": "B"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern"})
        client.post("/api/v1/clusters/2/plants", json={"species": "Monstera"})
        resp = client.get("/api/v1/plants?cluster_id=2")
        species = [p["species"] for p in resp.json()["plants"]]
        assert species == ["Monstera"]

    def test_filter_by_category(self, client):
        """category query param restricts results to one category."""
        client.post("/api/v1/clusters", json={"name": "A"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern", "category": "tropical"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Cactus", "category": "succulent"})
        resp = client.get("/api/v1/plants?category=succulent")
        species = [p["species"] for p in resp.json()["plants"]]
        assert species == ["Cactus"]

    def test_pagination_with_cursor(self, client):
        """limit + cursor walks the rows id-ascending."""
        client.post("/api/v1/clusters", json={"name": "A"})
        for n in range(5):
            client.post("/api/v1/clusters/1/plants", json={"species": f"P{n}"})
        # First page of 2
        resp = client.get("/api/v1/plants?limit=2")
        data = resp.json()
        assert len(data["plants"]) == 2
        assert data["next_cursor"] == data["plants"][-1]["id"]
        # Second page using cursor
        resp = client.get(f"/api/v1/plants?limit=2&cursor={data['next_cursor']}")
        data2 = resp.json()
        assert len(data2["plants"]) == 2
        assert data2["plants"][0]["id"] > data["plants"][-1]["id"]
        # Last page is partial, no cursor.
        resp = client.get(f"/api/v1/plants?limit=2&cursor={data2['next_cursor']}")
        data3 = resp.json()
        assert len(data3["plants"]) == 1
        assert data3["next_cursor"] is None

    def test_limit_validation(self, client):
        """limit must be 1..500."""
        resp = client.get("/api/v1/plants?limit=0")
        assert resp.status_code == 422
        resp = client.get("/api/v1/plants?limit=501")
        assert resp.status_code == 422
