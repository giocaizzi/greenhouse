"""Tests for the alert inbox API."""

from http import HTTPStatus
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from greenhouse_core.models import ActivitySource, EntityType
from greenhouse_core.repository import IrrigationRepository

# A placeholder source outside the ActivitySource vocabulary: the column stores any string.
TEST_SOURCE = cast("ActivitySource", "test")


class TestAlertList:
    """Tests for alert list."""

    def test_list_empty(self, client: TestClient) -> None:
        """List empty."""
        resp = client.get("/api/v1/alerts")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["open_count"] == 0
        assert data["items"] == []

    def test_list_with_status_filter(self, client: TestClient) -> None:
        """List with status filter."""
        resp = client.get("/api/v1/alerts?status=open")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["items"] == []

    def test_list_with_cluster_filter(self, client: TestClient) -> None:
        """List with cluster filter."""
        resp = client.get("/api/v1/alerts?cluster_id=1")
        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["items"] == []


class TestAlertGetById:
    """Tests for alert get by id."""

    def test_get_not_found(self, client: TestClient) -> None:
        """Get not found."""
        resp = client.get("/api/v1/alerts/999")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_get_existing(self, app: FastAPI) -> None:
        """Get existing."""
        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                repo = IrrigationRepository(session)
                alert = repo.upsert_alert(
                    dedup_key="test::get::1",
                    source=TEST_SOURCE,
                    code="test_alert",
                    title="Test Alert",
                    message="test message",
                    severity="warning",
                    entity_type=EntityType.CLUSTER,
                    cluster_id=1,
                )
                session.commit()
                alert_id = alert.id
            finally:
                session.close()

            resp = client.get(f"/api/v1/alerts/{alert_id}")
            assert resp.status_code == HTTPStatus.OK
            data = resp.json()
            assert data["id"] == alert_id
            assert data["status"] == "open"
            assert data["title"] == "Test Alert"


class TestAlertAcknowledge:
    """Tests for alert acknowledge."""

    def test_acknowledge_not_found(self, client: TestClient) -> None:
        """Acknowledge not found."""
        resp = client.post("/api/v1/alerts/999/acknowledge")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_acknowledge_flow(self, app: FastAPI) -> None:
        """Acknowledge flow."""
        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                repo = IrrigationRepository(session)
                alert = repo.upsert_alert(
                    dedup_key="test::ack::1",
                    source=TEST_SOURCE,
                    code="test_alert",
                    title="Test Alert",
                    message="test message",
                    severity="warning",
                    entity_type=EntityType.CLUSTER,
                    cluster_id=1,
                )
                session.commit()
                alert_id = alert.id
            finally:
                session.close()

            resp = client.post(f"/api/v1/alerts/{alert_id}/acknowledge")
            assert resp.status_code == HTTPStatus.OK
            data = resp.json()
            assert data["status"] == "acknowledged"
            assert data["acknowledged_at"] is not None

            # Idempotent second call stays acknowledged
            resp = client.post(f"/api/v1/alerts/{alert_id}/acknowledge")
            assert resp.status_code == HTTPStatus.OK
            assert resp.json()["status"] == "acknowledged"


class TestAlertResolve:
    """Tests for alert resolve."""

    def test_resolve_not_found(self, client: TestClient) -> None:
        """Resolve not found."""
        resp = client.post("/api/v1/alerts/999/resolve")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_resolve_flow(self, app: FastAPI) -> None:
        """Resolve flow."""
        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                repo = IrrigationRepository(session)
                alert = repo.upsert_alert(
                    dedup_key="test::resolve::1",
                    source=TEST_SOURCE,
                    code="test_alert",
                    title="Test Alert",
                    message="test message",
                    severity="info",
                    entity_type=EntityType.CLUSTER,
                    cluster_id=1,
                )
                session.commit()
                alert_id = alert.id
            finally:
                session.close()

            resp = client.post(f"/api/v1/alerts/{alert_id}/resolve")
            assert resp.status_code == HTTPStatus.OK
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
    """Tests for alert sync cluster."""

    def test_sync_not_found(self, client: TestClient) -> None:
        """Sync not found."""
        resp = client.post("/api/v1/clusters/999/alerts/sync")
        assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_sync_returns_list_response(self, seeded_client: TestClient) -> None:
        """Sync returns list response."""
        resp = seeded_client.post("/api/v1/clusters/1/alerts/sync")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert "open_count" in data
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_sync_dedup_on_repeat(self, seeded_client: TestClient) -> None:
        """Calling sync twice must not duplicate alerts."""
        seeded_client.post("/api/v1/clusters/1/alerts/sync")
        resp1 = seeded_client.get("/api/v1/alerts?cluster_id=1")
        count1 = len(resp1.json()["items"])

        seeded_client.post("/api/v1/clusters/1/alerts/sync")
        resp2 = seeded_client.get("/api/v1/alerts?cluster_id=1")
        count2 = len(resp2.json()["items"])

        assert count2 == count1


class TestAlertSyncAll:
    """Tests for alert sync all."""

    def test_sync_all(self, seeded_client: TestClient) -> None:
        """Sync all."""
        resp = seeded_client.post("/api/v1/alerts/sync")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert "open_count" in data
        assert "items" in data

    def test_sync_all_open_count_badge(self, app: FastAPI) -> None:
        """Badge count must equal the number of open alerts."""
        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                repo = IrrigationRepository(session)
                repo.upsert_alert(
                    dedup_key="test::badge::1",
                    source=TEST_SOURCE,
                    code="badge_test",
                    title="Badge Test",
                    message="msg",
                    entity_type=EntityType.CLUSTER,
                    cluster_id=1,
                )
                repo.upsert_alert(
                    dedup_key="test::badge::2",
                    source=TEST_SOURCE,
                    code="badge_test",
                    title="Badge Test 2",
                    message="msg2",
                    entity_type=EntityType.CLUSTER,
                    cluster_id=1,
                )
                session.commit()
            finally:
                session.close()

            resp = client.get("/api/v1/alerts")
            data = resp.json()
            assert data["open_count"] == len([a for a in data["items"] if a["status"] == "open"])


class TestAlertListPagination:
    """Cursor pagination on GET /alerts."""

    def _seed_alerts(self, app: FastAPI, count: int) -> list[int]:
        ids: list[int] = []
        with app.state.session_factory() as session:
            repo = IrrigationRepository(session)
            for n in range(count):
                row = repo.upsert_alert(
                    dedup_key=f"test::pag::{n}",
                    source=TEST_SOURCE,
                    code="test_alert",
                    title=f"Alert {n}",
                    message=f"msg {n}",
                    severity="info",
                    entity_type=EntityType.CLUSTER,
                    cluster_id=1,
                    seen_at=1_700_000_000 + n,
                )
                session.commit()
                ids.append(row.id)
        return ids

    def test_pagination_walks_inbox_newest_first(self, app: FastAPI, client: TestClient) -> None:
        """Iterating with cursor visits each alert exactly once, newest first."""
        ids = self._seed_alerts(app, 5)
        seen: list[int] = []
        cursor: int | None = None
        for _ in range(5):
            url = "/api/v1/alerts?limit=2"
            if cursor is not None:
                url += f"&cursor={cursor}"
            resp = client.get(url)
            data = resp.json()
            page = [a["id"] for a in data["items"]]
            seen.extend(page)
            cursor = data["next_cursor"]
            if cursor is None:
                break
        # All 5 ids visited.
        assert sorted(seen) == sorted(ids)
        # Newest first within pages.
        assert seen == sorted(seen, reverse=True)

    def test_next_cursor_is_null_when_page_is_partial(self, app: FastAPI, client: TestClient) -> None:
        """The last (partial) page must return ``next_cursor=None``."""
        seeded = 3
        self._seed_alerts(app, seeded)
        resp = client.get("/api/v1/alerts?limit=5")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert len(data["items"]) == seeded
        assert data["next_cursor"] is None
