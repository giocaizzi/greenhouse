"""Tests for the .well-known OAuth metadata stubs.

These endpoints exist to satisfy MCP HTTP clients that eagerly probe OAuth
discovery before sending a configured bearer token. See the route module's
docstring for the upstream context.
"""

import pytest
from fastapi.testclient import TestClient


def test_oauth_protected_resource_returns_rfc9728_metadata(client: TestClient):
    """RFC 9728 probe gets 200 with valid metadata pointing at /mcp.

    With ``authorization_servers`` empty, conformant clients should stop
    OAuth discovery and apply the bearer they were already configured with.
    """
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["resource"].endswith("/mcp"), body
    assert body["authorization_servers"] == []
    assert body["bearer_methods_supported"] == ["header"]
    assert body["resource_name"] == "greenhouse"


def test_oauth_authorization_server_returns_json_404(client: TestClient):
    """RFC 8414 probe falls through to 404 — but as JSON, not the default
    ``{"detail": "Not Found"}`` that crashes Claude Code's MCP SDK during
    Zod validation.
    """
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 404, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert "error" in body, body
    assert body["error"] == "not_an_authorization_server"


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
    ],
)
def test_well_known_routes_do_not_require_authentication(client: TestClient, path: str):
    """OAuth discovery URLs must be reachable without credentials —
    that is by definition how unauthenticated clients learn how to
    authenticate. A 401 here would defeat the discovery probe entirely.
    """
    resp = client.get(path)
    assert resp.status_code != 401, resp.text


def test_well_known_paths_stay_out_of_openapi(app):
    """The .well-known stubs must not appear in OpenAPI — otherwise
    fastapi-mcp would expose them as MCP tools.
    """
    schema_paths = list(app.openapi().get("paths", {}).keys())
    leaked = [p for p in schema_paths if p.startswith("/.well-known")]
    assert not leaked, f"well-known paths leaked into OpenAPI: {leaked}"
