"""Tests for the alert inbox API."""


class TestAlertList:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["open_count"] == 0
        assert data["items"] == []

    def test_list_with_status_filter(self, client):
        resp = client.get("/api/v1/alerts?status=open")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_list_with_cluster_filter(self, client):
        resp = client.get("/api/v1/alerts?cluster_id=1")
        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestAlertGetById:
    def test_get_not_found(self, client):
        resp = client.get("/api/v1/alerts/999")
        assert resp.status_code == 404

    def test_get_existing(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                from tuya_irrigation_core.repository import IrrigationRepository

                repo = IrrigationRepository(session)
                alert = repo.upsert_alert(
                    dedup_key="test::get::1",
                    source="test",
                    code="test_alert",
                    title="Test Alert",
                    message="test message",
                    severity="warning",
                    entity_type="cluster",
                    cluster_id=1,
                )
                session.commit()
                alert_id = alert.id
            finally:
                session.close()

            resp = client.get(f"/api/v1/alerts/{alert_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == alert_id
            assert data["status"] == "open"
            assert data["title"] == "Test Alert"


class TestAlertAcknowledge:
    def test_acknowledge_not_found(self, client):
        resp = client.post("/api/v1/alerts/999/acknowledge")
        assert resp.status_code == 404

    def test_acknowledge_flow(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                from tuya_irrigation_core.repository import IrrigationRepository

                repo = IrrigationRepository(session)
                alert = repo.upsert_alert(
                    dedup_key="test::ack::1",
                    source="test",
                    code="test_alert",
                    title="Test Alert",
                    message="test message",
                    severity="warning",
                    entity_type="cluster",
                    cluster_id=1,
                )
                session.commit()
                alert_id = alert.id
            finally:
                session.close()

            resp = client.post(f"/api/v1/alerts/{alert_id}/acknowledge")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "acknowledged"
            assert data["acknowledged_at"] is not None

            # Idempotent second call stays acknowledged
            resp = client.post(f"/api/v1/alerts/{alert_id}/acknowledge")
            assert resp.status_code == 200
            assert resp.json()["status"] == "acknowledged"


class TestAlertResolve:
    def test_resolve_not_found(self, client):
        resp = client.post("/api/v1/alerts/999/resolve")
        assert resp.status_code == 404

    def test_resolve_flow(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                from tuya_irrigation_core.repository import IrrigationRepository

                repo = IrrigationRepository(session)
                alert = repo.upsert_alert(
                    dedup_key="test::resolve::1",
                    source="test",
                    code="test_alert",
                    title="Test Alert",
                    message="test message",
                    severity="info",
                    entity_type="cluster",
                    cluster_id=1,
                )
                session.commit()
                alert_id = alert.id
            finally:
                session.close()

            resp = client.post(f"/api/v1/alerts/{alert_id}/resolve")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "resolved"
            assert data["resolved_at"] is not None

            # open_count must not include it
            resp = client.get("/api/v1/alerts")
            assert resp.json()["open_count"] == 0

            # Visible when filtering by resolved
            resp = client.get("/api/v1/alerts?status=resolved")
            ids = [a["id"] for a in resp.json()["items"]]
            assert alert_id in ids


class TestAlertSyncCluster:
    def test_sync_not_found(self, client):
        resp = client.post("/api/v1/clusters/999/alerts/sync")
        assert resp.status_code == 404

    def test_sync_returns_list_response(self, seeded_client):
        resp = seeded_client.post("/api/v1/clusters/1/alerts/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert "open_count" in data
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_sync_dedup_on_repeat(self, seeded_client):
        """Calling sync twice must not duplicate alerts."""
        seeded_client.post("/api/v1/clusters/1/alerts/sync")
        resp1 = seeded_client.get("/api/v1/alerts?cluster_id=1")
        count1 = len(resp1.json()["items"])

        seeded_client.post("/api/v1/clusters/1/alerts/sync")
        resp2 = seeded_client.get("/api/v1/alerts?cluster_id=1")
        count2 = len(resp2.json()["items"])

        assert count2 == count1


class TestAlertSyncAll:
    def test_sync_all(self, seeded_client):
        resp = seeded_client.post("/api/v1/alerts/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert "open_count" in data
        assert "items" in data

    def test_sync_all_open_count_badge(self, app):
        """Badge count must equal the number of open alerts."""
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                from tuya_irrigation_core.repository import IrrigationRepository

                repo = IrrigationRepository(session)
                repo.upsert_alert(
                    dedup_key="test::badge::1",
                    source="test",
                    code="badge_test",
                    title="Badge Test",
                    message="msg",
                    entity_type="cluster",
                    cluster_id=1,
                )
                repo.upsert_alert(
                    dedup_key="test::badge::2",
                    source="test",
                    code="badge_test",
                    title="Badge Test 2",
                    message="msg2",
                    entity_type="cluster",
                    cluster_id=1,
                )
                session.commit()
            finally:
                session.close()

            resp = client.get("/api/v1/alerts")
            data = resp.json()
            assert data["open_count"] == len([a for a in data["items"] if a["status"] == "open"])
