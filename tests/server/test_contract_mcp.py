"""Characterization: the MCP tool surface published at ``/mcp`` (G2).

``fastapi-mcp`` 0.4 derives the tools once, in ``FastApiMCP.setup_server``, from the
app's OpenAPI document and keeps them on the instance (``app.state.mcp``):

- ``mcp.tools`` — ``list[mcp.types.Tool]`` returned verbatim by the ``tools/list``
  handler (name = operationId = route function name, description = route
  docstring + fastapi-mcp's response rendering, ``inputSchema`` = path/query
  params + request body);
- ``mcp.operation_map`` — tool name → ``{"path", "method", "parameters",
  "request_body"}`` used to dispatch ``tools/call`` to the inner ``/api/v1`` route.

The golden ``contracts/mcp_tools.json`` keeps the order fastapi-mcp produces (tool
order = OpenAPI path order; key order = schema order) because that is what an MCP
client receives. The bearer-token gate (401/503 + detail strings + accept path) is
already pinned by ``tests/server/test_mcp.py``; this module only adds what that file
leaves open: the exact JSON bodies across every mounted method, the non-Bearer
scheme, and the dropped ``WWW-Authenticate`` header (observed bug).
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import mcp.types as mcp_types
import pytest
from fastapi.testclient import TestClient

from golden import assert_golden, install_offline_weather
from server.conftest import _make_stubbed_app

# sha256 of the name-sorted, key-sorted ``exclude_none`` tool dump recorded in
# refactor/00-contracts.md at the Phase-0 baseline (main @ a1b2622); re-recorded only by
# reviewed behavior-change commits (`fix(drift|consistency): …`) that also regenerate the golden.
PHASE0_MCP_TOOLS_SHA256 = (
    "523c6ed21685e8eb854f2572f3449603a2dfdfde34091f91e931c830e592bbc0"  # gitleaks:allow — content hash, not a secret
)

_TOKEN = "contract-mcp-token"


@pytest.fixture
def mcp_app(clean_env):
    application, engine = _make_stubbed_app(bypass_auth=False)
    install_offline_weather(application)
    yield application
    engine.dispose()


def _tool_dumps(tools) -> list[dict]:
    return [t.model_dump(mode="json", exclude_none=True) for t in tools]


def _surface(app) -> dict:
    mcp = app.state.mcp
    return {
        "server": {
            "name": mcp.name,
            "description": mcp.description,
            "server_name": mcp.server.name,
            "describe_all_responses": mcp._describe_all_responses,
            "describe_full_response_schema": mcp._describe_full_response_schema,
            "include_operations": mcp._include_operations,
            "exclude_operations": mcp._exclude_operations,
            "include_tags": mcp._include_tags,
            "exclude_tags": mcp._exclude_tags,
        },
        "tools": _tool_dumps(mcp.tools),
        "operation_map": {
            name: {"method": entry["method"], "path": entry["path"]} for name, entry in mcp.operation_map.items()
        },
    }


def test_mcp_surface_matches_golden(mcp_app):
    """Server metadata, every tool (name/description/inputSchema) and the dispatch map."""
    text = json.dumps(_surface(mcp_app), indent=1, ensure_ascii=False) + "\n"
    assert_golden("contracts/mcp_tools.json", text)


def test_mcp_tools_match_phase0_baseline_fingerprint(mcp_app):
    tools = sorted(_tool_dumps(mcp_app.state.mcp.tools), key=lambda t: t["name"])
    blob = json.dumps(tools, sort_keys=True, indent=1)
    assert hashlib.sha256(blob.encode()).hexdigest() == PHASE0_MCP_TOOLS_SHA256


def test_tools_list_handler_returns_exactly_mcp_tools(mcp_app):
    """The protocol-level ``tools/list`` answer is the same list, same order."""
    mcp = mcp_app.state.mcp
    handler = mcp.server.request_handlers[mcp_types.ListToolsRequest]
    result = asyncio.run(handler(mcp_types.ListToolsRequest(method="tools/list")))
    assert _tool_dumps(result.root.tools) == _tool_dumps(mcp.tools)
    assert len(result.root.tools) == 84


def test_tool_names_are_operation_ids_one_to_one(mcp_app):
    mcp = mcp_app.state.mcp
    op_ids = [op["operationId"] for item in mcp_app.openapi()["paths"].values() for op in item.values()]
    assert [t.name for t in mcp.tools] == op_ids
    assert list(mcp.operation_map) == op_ids


# --- auth gate: only what tests/server/test_mcp.py does not already pin ------


@pytest.fixture
def gated_client(clean_env):
    application, engine = _make_stubbed_app(bypass_auth=True, mcp_token=_TOKEN)
    install_offline_weather(application)
    yield TestClient(application, raise_server_exceptions=False)
    engine.dispose()


@pytest.fixture
def unconfigured_client(clean_env):
    application, engine = _make_stubbed_app(bypass_auth=True, mcp_token=None)
    install_offline_weather(application)
    yield TestClient(application, raise_server_exceptions=False)
    engine.dispose()


_BAD_HEADERS = {
    "missing": {},
    "wrong-token": {"Authorization": "Bearer not-the-token"},
    "basic-scheme": {"Authorization": f"Basic {_TOKEN}"},
    "empty-bearer": {"Authorization": "Bearer "},
}


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("header_case", sorted(_BAD_HEADERS))
def test_mcp_401_current_behavior_drops_www_authenticate_header(gated_client, method, header_case):
    """Pins current (buggy) behavior: ``require_mcp_token`` raises 401 with
    ``WWW-Authenticate: Bearer`` but the JSON branch of the global HTTPException handler
    drops ``exc.headers``, so the header never reaches the client — see REFACTOR_NOTES.md.
    """
    resp = gated_client.request(method, "/mcp", headers=_BAD_HEADERS[header_case])
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid MCP token"}
    assert resp.headers["content-type"] == "application/json"
    assert "www-authenticate" not in resp.headers


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": f"Bearer {_TOKEN}"}])
def test_mcp_503_body_when_token_unset_on_every_method(unconfigured_client, method, headers):
    resp = unconfigured_client.request(method, "/mcp", headers=headers)
    assert resp.status_code == 503
    assert resp.json() == {"detail": "MCP auth not configured"}
    assert "www-authenticate" not in resp.headers
