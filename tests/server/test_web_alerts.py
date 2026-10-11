"""Web UI tests for /alerts pages and fragments."""

import json
from http import HTTPStatus

from fastapi.testclient import TestClient

from greenhouse_core.repository import IrrigationRepository


def _seed_alert(app, *, dedup_key="test::web::1", severity="warning", status="open"):
    """Helper: insert an alert directly via the repo and return its id."""
    session = app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        alert = repo.upsert_alert(
            dedup_key=dedup_key,
            source="test",
            code="test_alert",
            title="Test Alert Title",
            message="Something went wrong with the sensor.",
            severity=severity,
            entity_type="cluster",
            cluster_id=1,
        )
        if status == "acknowledged":
            repo.acknowledge_alert(alert.id)
        elif status == "resolved":
            repo.resolve_alert(alert.id)
        session.commit()
        return alert.id
    finally:
        session.close()


class TestAlertListPage:
    def test_renders_empty_state(self, client):
        resp = client.get("/alerts")
        assert resp.status_code == HTTPStatus.OK
        assert "Alerts" in resp.text
        assert "No alerts" in resp.text

    def test_renders_alert_rows(self, app):
        with TestClient(app, raise_server_exceptions=False) as client:
            _seed_alert(app, dedup_key="web::list::1")
            resp = client.get("/alerts")
            assert resp.status_code == HTTPStatus.OK
            assert "Test Alert Title" in resp.text
            assert "Something went wrong" in resp.text

    def test_filter_open(self, app):
        with TestClient(app, raise_server_exceptions=False) as client:
            _seed_alert(app, dedup_key="web::open::1", status="open")
            resp = client.get("/alerts?status=open")
            assert resp.status_code == HTTPStatus.OK
            assert "Test Alert Title" in resp.text

    def test_filter_resolved_excludes_open(self, app):
        with TestClient(app, raise_server_exceptions=False) as client:
            _seed_alert(app, dedup_key="web::res::1", status="resolved")
            resp = client.get("/alerts?status=open")
            assert resp.status_code == HTTPStatus.OK
            # The open-only view should NOT show resolved alerts
            assert "No alerts" in resp.text

    def test_segmented_filter_chips_rendered(self, client):
        resp = client.get("/alerts")
        assert resp.status_code == HTTPStatus.OK
        assert 'href="/alerts?status=open"' in resp.text
        assert 'href="/alerts?status=acknowledged"' in resp.text
        assert 'href="/alerts?status=resolved"' in resp.text

    def test_sync_button_present(self, client):
        resp = client.get("/alerts")
        assert resp.status_code == HTTPStatus.OK
        assert "hx-post" in resp.text
        assert "/alerts/sync" in resp.text


class TestAlertBadge:
    def test_badge_empty_when_no_open_alerts(self, client):
        resp = client.get("/alerts/badge")
        assert resp.status_code == HTTPStatus.OK
        assert resp.text == ""

    def test_badge_shows_count_when_open(self, app):
        with TestClient(app, raise_server_exceptions=False) as client:
            _seed_alert(app, dedup_key="web::badge::1", status="open")
            _seed_alert(app, dedup_key="web::badge::2", status="open")
            resp = client.get("/alerts/badge")
            assert resp.status_code == HTTPStatus.OK
            assert "bell__count" in resp.text
            assert "2" in resp.text

    def test_badge_omits_resolved_alerts(self, app):
        with TestClient(app, raise_server_exceptions=False) as client:
            _seed_alert(app, dedup_key="web::badge::res", status="resolved")
            resp = client.get("/alerts/badge")
            assert resp.status_code == HTTPStatus.OK
            assert resp.text == ""


class TestAlertAckFlow:
    def test_ack_returns_row_fragment(self, app):
        with TestClient(app, raise_server_exceptions=False) as client:
            alert_id = _seed_alert(app, dedup_key="web::ack::1", status="open")
            resp = client.post(
                f"/alerts/{alert_id}/ack",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == HTTPStatus.OK
            assert "acknowledged" in resp.text
            assert "HX-Toast" in resp.headers

            toast = json.loads(resp.headers["HX-Toast"])
            assert toast["severity"] == "success"

    def test_ack_changes_status_in_db(self, app):
        with TestClient(app, raise_server_exceptions=False) as client:
            alert_id = _seed_alert(app, dedup_key="web::ack::db::1", status="open")
            client.post(f"/alerts/{alert_id}/ack")
            # Verify via API
            resp = client.get(f"/api/v1/alerts/{alert_id}")
            assert resp.json()["status"] == "acknowledged"


class TestAlertResolveFlow:
    def test_resolve_returns_row_fragment(self, app):
        with TestClient(app, raise_server_exceptions=False) as client:
            alert_id = _seed_alert(app, dedup_key="web::resolve::1", status="open")
            resp = client.post(
                f"/alerts/{alert_id}/resolve",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == HTTPStatus.OK
            assert "resolved" in resp.text
            assert "HX-Toast" in resp.headers

    def test_resolve_changes_status_in_db(self, app):
        with TestClient(app, raise_server_exceptions=False) as client:
            alert_id = _seed_alert(app, dedup_key="web::resolve::db::1")
            client.post(f"/alerts/{alert_id}/resolve")
            resp = client.get(f"/api/v1/alerts/{alert_id}")
            assert resp.json()["status"] == "resolved"
