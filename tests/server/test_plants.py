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
            assert _json.loads(moved_rows[0].payload_json) == {"from_cluster_id": 1, "to_cluster_id": 2}

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
