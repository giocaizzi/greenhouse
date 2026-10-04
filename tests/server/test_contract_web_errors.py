"""Characterization: error pages and the auth wall in front of the web UI.

Goldens in ``golden/web/errors/<case>.html`` (request line, status, pinned headers, body):

* unknown paths / wrong methods — HTML vs JSON per ``Accept`` / ``HX-Request`` / ``/api`` prefix;
* the anonymous-browser redirect to ``/login?next=…`` (303, or 204 + ``HX-Redirect`` for HTMX)
  on the real-auth app (``app_real_auth`` / ``anonymous_client`` fixtures from the server conftest);
* the JSON 401 from ``/api/v1`` and ``/mcp`` — which today *drops* the ``WWW-Authenticate``
  header set by ``AuthError`` / ``require_mcp_token`` (observed bug, pinned below).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from golden import assert_golden, install_offline_weather
from server import test_contract_web_html as _web
from server.test_contract_web_html import (
    INDOOR,
    render_response,
)

HTML = {"Accept": "text/html,application/xhtml+xml"}
JSON = {"Accept": "application/json"}
HX = {"HX-Request": "true"}

# Shared fixtures (pytest collects fixtures bound to module attributes).
make_client = _web.make_client
seeded_get_client = _web.seeded_get_client
web_env = _web.web_env


@pytest.mark.parametrize(
    ("name", "method", "url", "headers"),
    [
        ("unknown_path__html", "GET", "/no/such/page", HTML),
        ("unknown_path__json", "GET", "/no/such/page", JSON),
        ("unknown_path__hx", "GET", "/no/such/page", HX),
        ("unknown_api_path__html", "GET", "/api/v1/no-such-thing", HTML),
        ("method_not_allowed__html", "PUT", "/clusters", HTML),
        ("method_not_allowed__json", "PUT", "/clusters", JSON),
        ("route_404__json_accept", "GET", "/clusters/999", JSON),
        ("route_422__json_accept", "GET", "/clusters/not-a-number", JSON),
        ("form_422__hx", "POST", "/clusters", HX),
    ],
)
def test_error_page_golden(name, method, url, headers, seeded_get_client):
    """Which handler answers (HTML error page / HX partial / JSON) — by path, not by ``Accept``."""
    resp = seeded_get_client.request(method, url, headers=headers, follow_redirects=False)
    assert_golden(f"web/errors/{name}.html", render_response(method, url, headers, resp))


@pytest.fixture
def anon(web_env, app_real_auth):
    """``anonymous_client`` on the real-auth app, under the frozen clock with offline weather."""
    install_offline_weather(app_real_auth)
    return TestClient(app_real_auth, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("name", "method", "url", "headers", "cookies"),
    [
        ("anonymous_page__redirects_to_login", "GET", f"/clusters/{INDOOR}", HTML, {}),
        ("anonymous_page_with_query__redirects", "GET", "/alerts?status=open&cluster_id=1", HTML, {}),
        ("anonymous_page__hx_redirect", "GET", "/health/badge", HX, {}),
        ("anonymous_post__redirects_to_login", "POST", "/check", HTML, {}),
        ("anonymous_root__redirects_to_login", "GET", "/", HTML, {}),
        ("garbage_session_cookie__redirects", "GET", "/vacation", HTML, {"greenhouse_session": "not-a-jwt"}),
        ("anonymous_login_page", "GET", "/login?next=/vacation", HTML, {}),
        ("anonymous_api__json_401", "GET", "/api/v1/clusters", HTML, {}),
        ("unknown_path__anonymous", "GET", "/no/such/page", HTML, {}),
    ],
)
def test_auth_wall_golden(name, method, url, headers, cookies, anon):
    """Anonymous browser → 303 ``/login?next=…`` (HTMX: 204 + ``HX-Redirect``); API → JSON 401."""
    for key, value in cookies.items():
        anon.cookies.set(key, value)
    resp = anon.request(method, url, headers=headers, follow_redirects=False)
    assert_golden(f"web/errors/{name}.html", render_response(method, url, headers, resp))


def test_session_cookie_name_is_the_one_the_garbage_case_uses(app_real_auth):
    """Guards the case above: it must exercise the real cookie, not an ignored one."""
    assert app_real_auth.state.settings.auth_cookie_name == "greenhouse_session"


def test_json_401_current_behavior_drops_www_authenticate(web_env, anon, make_client):
    """Pins current (buggy) behavior: the JSON 401 lacks ``WWW-Authenticate`` — see REFACTOR_NOTES.md.

    ``AuthError`` (``greenhouse_server/auth.py``) and ``require_mcp_token`` (``app.py``) both set
    ``WWW-Authenticate: Bearer``, but the global ``HTTPException`` handler's JSON branch
    (``web/exception_handlers.py:41-44``) rebuilds the response from ``detail`` + status only and
    drops ``exc.headers``. RFC 9110 §11.6.1 requires the header on a 401.
    """
    api = anon.get("/api/v1/clusters")
    assert api.status_code == 401
    assert api.json() == {"detail": "Not authenticated"}
    assert "www-authenticate" not in api.headers

    bad_bearer = anon.get("/api/v1/clusters", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad_bearer.status_code == 401
    assert "www-authenticate" not in bad_bearer.headers

    _, mcp_client = make_client(bypass_auth=False, mcp_token="contract-mcp-token")
    mcp = mcp_client.post(
        "/mcp",
        headers={"Authorization": "Bearer wrong", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert mcp.status_code == 401
    assert mcp.json() == {"detail": "Invalid MCP token"}
    assert "www-authenticate" not in mcp.headers


def test_unknown_path_current_behavior_returns_json_404_to_browsers(seeded_get_client):
    """Pins current behavior: an unknown path answers JSON even to a browser — see REFACTOR_NOTES.md.

    The ``web/exception_handlers.py`` docstring says HTML is rendered for ``Accept: text/html`` /
    ``HX-Request`` calls, but (a) the handler branches on the *path* only (``/api/``, ``/mcp``),
    and (b) router-level 404/405 are Starlette ``HTTPException``s, which the handler registered
    for FastAPI's subclass never sees — so Starlette's default JSON body is served. Conversely a
    route-raised 404 renders the HTML error page even for ``Accept: application/json``.
    """
    unknown = seeded_get_client.get("/no/such/page", headers=HTML)
    assert (unknown.status_code, unknown.json()) == (404, {"detail": "Not Found"})
    wrong_method = seeded_get_client.put("/clusters", headers=HTML)
    assert (wrong_method.status_code, wrong_method.json()) == (405, {"detail": "Method Not Allowed"})
    route_404 = seeded_get_client.get("/clusters/999", headers=JSON)
    assert route_404.status_code == 404
    assert route_404.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize(("name", "url"), [("auth_disabled__dashboard", "/"), ("auth_disabled__login_form", "/login")])
def test_auth_disabled_mode_golden(name, url, make_client):
    """``auth_enabled=False`` dev mode: no auth wall, and the chrome hides the Sign-out button."""
    _, client = make_client(bypass_auth=False, auth_enabled=False)
    resp = client.get(url, follow_redirects=False)
    assert_golden(f"web/errors/{name}.html", render_response("GET", url, {}, resp))
