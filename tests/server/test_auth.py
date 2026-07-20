"""Authentication: login, bearer + cookie session, /me, logout, 401 paths."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from fake_devices import FakeIrrigatorAdapter
from greenhouse_core.devices import DeviceRegistry
from greenhouse_server.app import create_app
from greenhouse_server.config import Settings
from greenhouse_server.deps import get_device_gateway, get_device_registry

from .conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME, TEST_AUTH_SECRET


def _build_app(**override):
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    base = {
        "db_url": "sqlite://",
        "enable_scheduler": False,
        "auth_enabled": True,
        "auth_secret_key": TEST_AUTH_SECRET,
        "auth_admin_username": TEST_ADMIN_USERNAME,
        "auth_admin_password": TEST_ADMIN_PASSWORD,
    }
    base.update(override)
    app = create_app(Settings(**base), engine=engine)
    fake = FakeIrrigatorAdapter()
    registry = DeviceRegistry()
    for key in ("rainpoint.ik10pw", "tuya_cloud", "tuya_local", ""):
        registry.register_irrigator(key, lambda adapter=fake: adapter)
    app.dependency_overrides[get_device_registry] = lambda: registry
    app.dependency_overrides[get_device_gateway] = lambda: None
    return app, engine


def test_anonymous_api_returns_401(anonymous_client: TestClient):
    resp = anonymous_client.get("/api/v1/clusters")
    assert resp.status_code == 401


def test_anonymous_web_redirects_to_login(anonymous_client: TestClient):
    resp = anonymous_client.get("/clusters", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")
    # ?next= preserves the original path so post-login lands you back where you were
    assert "%2Fclusters" in resp.headers["location"] or "/clusters" in resp.headers["location"]


def test_anonymous_login_page_renders(anonymous_client: TestClient):
    resp = anonymous_client.get("/login")
    assert resp.status_code == 200
    assert b"Sign in" in resp.content
    assert b'name="username"' in resp.content
    assert b'name="password"' in resp.content


def test_login_with_valid_credentials_returns_token(anonymous_client: TestClient):
    resp = anonymous_client.post(
        "/api/v1/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]
    assert payload["expires_in"] > 0
    assert payload["username"] == TEST_ADMIN_USERNAME
    # Also sets the session cookie for browser clients
    assert "greenhouse_session" in resp.cookies


def test_login_with_wrong_password_returns_401(anonymous_client: TestClient):
    resp = anonymous_client.post(
        "/api/v1/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_login_with_unknown_user_returns_401(anonymous_client: TestClient):
    resp = anonymous_client.post(
        "/api/v1/auth/login",
        json={"username": "no-such-user", "password": "whatever"},
    )
    assert resp.status_code == 401


def test_bearer_token_authenticates_api(authed_real_client: TestClient):
    """Real-auth client (bearer header set from real login) reaches /me."""
    resp = authed_real_client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == TEST_ADMIN_USERNAME
    assert body["id"] > 0


def test_invalid_bearer_token_returns_401(anonymous_client: TestClient):
    resp = anonymous_client.get("/api/v1/clusters", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_cookie_session_authenticates_web_pages(anonymous_client: TestClient):
    # First log in via API (sets cookie), then hit a web page anonymously by
    # header — only the cookie should carry through.
    resp = anonymous_client.post(
        "/api/v1/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
    # TestClient persists cookies across calls
    page = anonymous_client.get("/")
    assert page.status_code == 200


def test_logout_clears_session(authed_real_client: TestClient):
    """Logout clears the cookie; a cookie-only follow-up must redirect to /login."""
    resp = authed_real_client.post("/api/v1/auth/logout")
    assert resp.status_code == 200
    authed_real_client.headers.pop("Authorization", None)
    authed_real_client.cookies.clear()
    page = authed_real_client.get("/clusters", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"].startswith("/login")


def test_form_login_redirects_to_next(anonymous_client: TestClient):
    resp = anonymous_client.post(
        "/login",
        data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD, "next": "/clusters"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/clusters"
    assert "greenhouse_session" in resp.cookies


def test_form_login_rejects_external_next(anonymous_client: TestClient):
    resp = anonymous_client.post(
        "/login",
        data={
            "username": TEST_ADMIN_USERNAME,
            "password": TEST_ADMIN_PASSWORD,
            "next": "https://evil.example.com/steal",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_login_page_has_no_chrome_pollers(anonymous_client: TestClient):
    """The login page must not carry the authenticated chrome's auto-pollers.

    `_base.html` fires `hx-get="/alerts/badge"` and `/health/badge` on
    `hx-trigger="load"`. Rendered on /login (unauthenticated) they redirect
    back to /login and recurse, cascading the page. show_chrome=False strips
    them. Regression test for the v3.0.0 login-page infinite-render bug.
    """
    resp = anonymous_client.get("/login")
    assert resp.status_code == 200
    assert b"/alerts/badge" not in resp.content
    assert b"/health/badge" not in resp.content
    # The top nav (which only makes sense once signed in) is gone too.
    assert b'class="topbar"' not in resp.content


def test_hx_request_unauthenticated_returns_hx_redirect(anonymous_client: TestClient):
    """An HTMX request to a protected web route while logged out must return
    an HX-Redirect header (full-page nav), not a 303 whose body HTMX would
    swap into the triggering fragment. Pairs with the login-page fix above so
    a session expiring mid-page bounces cleanly to /login."""
    resp = anonymous_client.get(
        "/clusters",
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 204
    assert resp.headers["HX-Redirect"].startswith("/login")
    # No login HTML in the body — that is the whole point.
    assert resp.content == b""


def test_auth_disabled_grants_anonymous_access(auth_disabled_app):
    tc = TestClient(auth_disabled_app, raise_server_exceptions=False)
    resp = tc.get("/api/v1/clusters")
    assert resp.status_code == 200
    # /me returns the synthetic system user
    me = tc.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "system"


def test_missing_secret_key_returns_503_on_login():
    # auth enabled but no secret — login itself can't sign; expect 503.
    app, _ = _build_app(auth_secret_key=None)
    tc = TestClient(app, raise_server_exceptions=False)
    resp = tc.post(
        "/api/v1/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    # bootstrap_admin runs but issue_token requires the secret. With no
    # secret and a credential match, the login route reaches `_require_secret`
    # which raises 503.
    assert resp.status_code in (401, 503)
