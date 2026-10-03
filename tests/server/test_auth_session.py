"""An authenticated request opens one DB session, shared by the auth dependency and the handler."""

from __future__ import annotations

from fastapi.testclient import TestClient

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
    app, _ = _build_app()
    with TestClient(app) as client:
        token = client.post(
            "/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD}
        ).json()["access_token"]
        opened = _counting_factory(app)
        resp = client.get("/api/v1/clusters", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    # Web chrome reads open their own short-lived session; the API request opens exactly one.
    assert len(opened) == 1


def test_auth_dependency_and_handler_share_the_session():
    from greenhouse_server.auth import _session_from_app
    from greenhouse_server.deps import get_session

    assert _session_from_app is get_session
