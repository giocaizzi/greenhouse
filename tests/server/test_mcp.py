"""Smoke tests for the MCP interface mounted at /mcp."""

import ast
from pathlib import Path

from starlette.routing import Mount, Route


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
    route_dir = Path(__file__).resolve().parents[2] / "libs/tuya-irrigation-server/tuya_irrigation_server/routes"
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
        "/api/v1/clusters/{cluster_id}/irrigators": "tuya_device_id",
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
