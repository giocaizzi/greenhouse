"""An authenticated request opens one DB session, shared by the auth dependency and the handler."""

from __future__ import annotations

from http import HTTPStatus

from fastapi.testclient import TestClient

from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.auth import _session_from_app
from greenhouse_server.deps import get_session

from .conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME
from .test_auth import _build_app


def _counting_factory(app):
    """Wrap ``app.state.session_factory`` so every opened session is recorded."""
    real = app.state.session_factory
    opened: list[object] = []

    def factory():
        session = real()
        opened.append(session)
        return session

    app.state.session_factory = factory
    return opened


def test_protected_api_request_opens_one_session():
    """Protected API request opens one session."""
    app, _ = _build_app()
    with TestClient(app) as client:
        token = client.post(
            "/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD}
        ).json()["access_token"]
        opened = _counting_factory(app)
        resp = client.get("/api/v1/clusters", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == HTTPStatus.OK
    # Web chrome reads open their own short-lived session; the API request opens exactly one.
    assert len(opened) == 1


def test_auth_dependency_and_handler_share_the_session():
    """Auth dependency and handler share the session."""
    assert _session_from_app is get_session


def test_anonymous_pages_hide_the_operational_banners_signed_in_pages_show_them(app_real_auth):
    """Dry-run / paused banners describe the system: only a signed-in viewer sees them."""
    session = app_real_auth.state.session_factory()
    IrrigationRepository(session).update_preferences(dry_run_global=True, scheduler_paused=True)
    session.commit()
    session.close()
    client = TestClient(app_real_auth, raise_server_exceptions=False)
    for url in ("/login", "/no/such/page"):
        page = client.get(url, headers={"Accept": "text/html"}).text
        assert "Dry-run mode is on" not in page, url
        assert 'data-banner="scheduler-paused"' not in page, url
    client.post("/login", data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    for url in ("/", "/no/such/page"):
        page = client.get(url, headers={"Accept": "text/html"}).text
        assert "Dry-run mode is on" in page, url
        assert 'data-banner="scheduler-paused"' in page, url
