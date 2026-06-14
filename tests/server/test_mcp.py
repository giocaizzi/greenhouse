"""Smoke tests for the MCP interface mounted at /mcp."""

import ast
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi_mcp.types import HTTPRequestInfo
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from starlette.routing import Mount, Route

from fake_devices import FakeIrrigatorAdapter, FakeSensorAdapter
from greenhouse_core.devices import DeviceRegistry
from greenhouse_server.app import create_app
from greenhouse_server.config import Settings
from greenhouse_server.deps import get_device_registry, get_tuya_cloud


def test_mcp_endpoint_is_mounted(app):
    """The MCP server must be mounted at /mcp."""
    paths = [route.path for route in app.routes if isinstance(route, Mount | Route)]
    assert any(path.startswith("/mcp") for path in paths), f"/mcp not found in routes: {paths}"


def test_mcp_does_not_leak_web_routes(app):
    """Web routes must stay out of the OpenAPI schema (and therefore out of MCP tools)."""
    schema = app.openapi()
    paths = list(schema.get("paths", {}).keys())
    assert paths, "OpenAPI schema unexpectedly empty"
    assert all(p.startswith("/api/v1") for p in paths), (
        f"Non-API paths leaked into the OpenAPI schema (would become MCP tools): {paths}"
    )


def test_mcp_exposes_every_api_operation_as_a_tool(app):
    """Every /api/v1 endpoint surfaces as an MCP tool with a non-empty input schema."""
    mcp = app.state.mcp
    op_paths = {entry["path"] for entry in mcp.operation_map.values()}
    schema_paths = set(app.openapi().get("paths", {}).keys())
    assert op_paths == schema_paths, (
        f"MCP tool coverage drifted from the OpenAPI schema:\n"
        f"  in schema, not in MCP: {schema_paths - op_paths}\n"
        f"  in MCP, not in schema: {op_paths - schema_paths}"
    )
    for tool in mcp.tools:
        assert tool.inputSchema is not None, f"MCP tool {tool.name} has no input schema"


def test_mcp_tool_names_fit_within_64_chars(app):
    """All MCP tool names must be ≤ 64 characters.

    Claude.ai connectors enforce a hard 64-char limit on tool names and reject
    the entire tools/list when any name exceeds it.  This test runs at CI time
    so a new route with a long function name never ships."""
    mcp = app.state.mcp
    over_limit = [tool.name for tool in mcp.tools if len(tool.name) > 64]
    assert not over_limit, "MCP tool names exceed the 64-character Claude.ai limit:\n  " + "\n  ".join(
        f"{name!r} ({len(name)} chars)" for name in over_limit
    )


def test_mcp_every_api_route_has_a_response_model(app):
    """Every /api/v1 endpoint must declare a response_model so MCP tools advertise a
    typed output. fastapi-mcp emits an "Example Response:" JSON block in the tool
    description only when a response_model is present; routes that return raw dicts
    (no response_model) do not get one. CSV export (binary download) is exempt.

    This is the load-bearing assertion for the MCP interface: untyped routes
    produce MCP tools an LLM cannot reason about safely."""
    mcp = app.state.mcp

    binary_exempt_paths = {"/api/v1/clusters/{cluster_id}/stats/export"}

    untyped = []
    for tool in mcp.tools:
        path = mcp.operation_map[tool.name]["path"]
        if path in binary_exempt_paths:
            continue
        if "Example Response:" not in (tool.description or ""):
            untyped.append((tool.name, path))

    assert not untyped, (
        "These MCP tools are missing a typed response schema (add response_model= to the route):\n  "
        + "\n  ".join(f"{name}  →  {path}" for name, path in untyped)
    )


def test_every_api_route_has_a_docstring():
    """Every `@router.*` endpoint must carry a docstring.

    fastapi-mcp uses the route's docstring (or `summary=`) as the MCP tool
    description an LLM sees when choosing tools — undocumented endpoints
    give the LLM only a function name to go on, which leads to wrong tool
    selection. This is enforced statically by walking the route source so
    that adding a new endpoint without a docstring breaks CI before it
    ever ships."""
    route_dir = Path(__file__).resolve().parents[2] / "libs/greenhouse-server/greenhouse_server/routes"
    missing = []
    for path in sorted(route_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            on_router = any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name)
                and d.func.value.id == "router"
                for d in node.decorator_list
            )
            if on_router and not ast.get_docstring(node):
                missing.append(f"{path.name}::{node.name}")
    assert not missing, "API endpoints missing a docstring:\n  " + "\n  ".join(missing)


def test_mcp_request_bodies_carry_their_field_schemas(app):
    """Endpoints with a Pydantic request body must surface those fields in the MCP
    tool's inputSchema, so an LLM client knows the call shape."""
    mcp = app.state.mcp

    body_carrying_paths = {
        "/api/v1/clusters": "name",
        "/api/v1/clusters/{cluster_id}/plants": "species",
        "/api/v1/clusters/{cluster_id}/sensors": "tuya_device_id",
        "/api/v1/clusters/{cluster_id}/irrigator": "tuya_device_id",
        "/api/v1/clusters/{cluster_id}/config": "mode",
        "/api/v1/clusters/{cluster_id}/irrigate": "dry_run",
        "/api/v1/sync": "hours",
        "/api/v1/irrigators/{irrigator_id}/log-manual": "minutes",
    }

    by_path: dict[str, list[str]] = {}
    for tool in mcp.tools:
        path = mcp.operation_map[tool.name]["path"]
        props = list((tool.inputSchema or {}).get("properties", {}).keys())
        by_path.setdefault(path, []).append(props[0] if props else "")
        by_path[path] = list({*by_path[path], *props})

    for path, expected_field in body_carrying_paths.items():
        props = by_path.get(path, [])
        assert expected_field in props, (
            f"MCP tool for {path} missing request-body field {expected_field!r}; got {props!r}"
        )


# --- /mcp bearer-token auth ---------------------------------------------------


_VALID_TOKEN = "test-mcp-bearer-token"


def _build_mcp_client(*, mcp_token: str | None) -> TestClient:
    """Build a TestClient bound to a fresh app configured with the given MCP token.

    Auth is fully wired (secret key + an admin bootstrap) so we can test how
    `require_user` behaves on the inner /api/v1 routes when fastapi-mcp
    forwards the MCP bearer header — including the negative case where a
    JWT-decode failure must still return 401, not 503.
    """
    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    settings = Settings(
        db_url="sqlite://",
        enable_scheduler=False,
        mcp_token=mcp_token,
        auth_enabled=True,
        auth_secret_key="unit-test-jwt-secret-do-not-use-in-prod-please",
        auth_admin_username="mcp-test-admin",
        auth_admin_password="mcp-test-admin-pw-123",
    )
    application = create_app(settings, engine=engine)

    fake_irrigator = FakeIrrigatorAdapter()
    fake_sensor = FakeSensorAdapter()
    registry = DeviceRegistry()
    for key in ("rainpoint.ik10pw", "tuya_cloud", "tuya_local", ""):
        registry.register_irrigator(key, lambda adapter=fake_irrigator: adapter)
    for key in ("tuya.tr301z", "soil_moisture", "temp_humidity", "light", ""):
        registry.register_sensor(key, lambda adapter=fake_sensor: adapter)
    application.dependency_overrides[get_device_registry] = lambda: registry
    application.dependency_overrides[get_tuya_cloud] = lambda: None

    return TestClient(application, raise_server_exceptions=False)


@pytest.fixture
def mcp_client_with_token() -> TestClient:
    """Client backed by an app with `mcp_token` set — the normal deployment path."""
    return _build_mcp_client(mcp_token=_VALID_TOKEN)


@pytest.fixture
def mcp_client_unconfigured() -> TestClient:
    """Client backed by an app with `mcp_token=None` — the fail-closed default."""
    return _build_mcp_client(mcp_token=None)


def test_mcp_rejects_request_without_authorization_header(mcp_client_with_token: TestClient):
    """Hitting /mcp without an Authorization header must 401, never pass through."""
    resp = mcp_client_with_token.get("/mcp")
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "Invalid MCP token"


def test_mcp_rejects_request_with_wrong_token(mcp_client_with_token: TestClient):
    """A bearer token that doesn't match the configured value must 401."""
    resp = mcp_client_with_token.get("/mcp", headers={"Authorization": "Bearer not-the-right-token"})
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "Invalid MCP token"


def test_mcp_accepts_request_with_correct_token(mcp_client_with_token: TestClient):
    """With the correct bearer token, the auth gate is transparent — the request
    reaches the MCP transport (whose handling of a bare GET isn't auth's concern).
    We assert only that the response is not 401/503, i.e. auth did not block it."""
    resp = mcp_client_with_token.get("/mcp", headers={"Authorization": f"Bearer {_VALID_TOKEN}"})
    assert resp.status_code not in (401, 503), (
        f"Valid token was rejected by the auth layer (status={resp.status_code}): {resp.text}"
    )


def test_mcp_returns_503_when_token_setting_is_unset(mcp_client_unconfigured: TestClient):
    """Fail-closed: with no MCP token configured, /mcp must NOT silently be open —
    every request, with or without an Authorization header, gets 503."""
    resp = mcp_client_unconfigured.get("/mcp")
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == "MCP auth not configured"

    # Even with what would have been a valid token, an unconfigured server must 503.
    resp = mcp_client_unconfigured.get("/mcp", headers={"Authorization": f"Bearer {_VALID_TOKEN}"})
    assert resp.status_code == 503, resp.text


# --- end-to-end tool invocation through the MCP bridge -----------------------
#
# Regression for the bug fixed in fix(auth): an MCP `tools/call` would 401
# because fastapi-mcp forwards the inbound `Authorization: Bearer <MCP_TOKEN>`
# header to the inner `/api/v1` endpoint, but `require_user` only knew how to
# decode JWTs and rejected the static MCP token as "Invalid session".
#
# The schema-only tests above pass even when tools/call is broken, so we
# explicitly exercise the inner HTTP call that fastapi-mcp performs on every
# tool invocation. This is the exact code path users hit from Claude / any
# MCP client — see fastapi_mcp.server.FastApiMCP._execute_api_tool.


def _invoke_mcp_tool(
    mcp_client: TestClient,
    *,
    tool_name: str,
    arguments: dict | None = None,
    bearer: str,
):
    """Drive the same code path fastapi-mcp executes on a real tools/call.

    Returns the list of content parts from `_execute_api_tool` — or re-raises
    whatever exception that method raised (e.g. inner endpoint 4xx/5xx, which
    `_execute_api_tool` converts to a generic Exception containing the status
    code and body for the MCP error envelope).
    """
    app = mcp_client.app
    mcp = app.state.mcp
    http_request_info = HTTPRequestInfo(
        method="POST",
        path="/mcp",
        headers={"authorization": f"Bearer {bearer}"},
        cookies={},
        query_params={},
        body=None,
    )
    return asyncio.run(
        mcp._execute_api_tool(
            client=mcp._http_client,
            tool_name=tool_name,
            arguments=arguments or {},
            operation_map=mcp.operation_map,
            http_request_info=http_request_info,
        )
    )


def test_mcp_tool_invocation_reaches_inner_endpoint(mcp_client_with_token: TestClient):
    """End-to-end: a forwarded MCP bearer must let the inner /api/v1 route serve
    a real response, not 401.

    Picks the `list_clusters` tool because it's a pure GET with no path/body
    params, so failure modes here are pinned to auth — not to argument
    marshalling. With an empty DB it must return an empty JSON array.
    """
    result = _invoke_mcp_tool(
        mcp_client_with_token,
        tool_name="list_clusters",
        bearer=_VALID_TOKEN,
    )
    assert len(result) == 1
    payload = json.loads(result[0].text)
    # What matters here is that the inner call returned a 2xx body, not an
    # MCP error envelope containing "Invalid session" / "401".
    assert payload == [], f"Expected empty cluster list, got: {payload!r}"


def test_mcp_tool_invocation_rejects_garbage_bearer(mcp_client_with_token: TestClient):
    """A forwarded header that is neither a valid JWT nor the MCP token must
    still 401 at the inner endpoint — the MCP-token short-circuit must not
    accidentally accept arbitrary bearers."""
    with pytest.raises(Exception) as excinfo:
        _invoke_mcp_tool(
            mcp_client_with_token,
            tool_name="list_clusters",
            bearer="not-the-real-token",
        )
    assert "401" in str(excinfo.value), f"Expected inner 401 in error, got: {excinfo.value!s}"
