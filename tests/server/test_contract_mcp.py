"""Characterization: the MCP tool surface published at ``/mcp``.

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
scheme, and the ``WWW-Authenticate`` header on every 401.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import mcp.types as mcp_types
import pytest
from fastapi.testclient import TestClient

from golden import assert_golden, install_offline_weather
from server.conftest import TEST_MCP_TOKEN, _make_stubbed_app

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi import FastAPI

# sha256 of the name-sorted, key-sorted ``exclude_none`` tool dump, first recorded at the
# original baseline (main @ a1b2622, hence "PHASE0"); re-recorded only by
# reviewed behavior-change commits (`fix(drift|consistency): …`) or description-only doc-contract
# commits (`docs(api): …`) that also regenerate the golden.
PHASE0_MCP_TOOLS_SHA256 = (
    "8bb313d3b6d40deef3bebb62c46ee778a89e9ca486c34b682cfed6983f8a105d"  # gitleaks:allow — content hash, not a secret
)

_TOKEN = TEST_MCP_TOKEN
MCP_TOOL_COUNT = 87  # one tool per /api/v1 operation


@pytest.fixture
def mcp_app(clean_env: Path) -> Iterator[FastAPI]:
    """An app with real auth and offline weather, for inspecting its MCP surface."""
    del clean_env  # requested only for its side effect
    application, engine = _make_stubbed_app(bypass_auth=False)
    install_offline_weather(application)
    yield application
    engine.dispose()


def _tool_dumps(tools: list[mcp_types.Tool]) -> list[dict[str, Any]]:
    return [t.model_dump(mode="json", exclude_none=True) for t in tools]


def _surface(app: FastAPI) -> dict[str, object]:
    mcp = app.state.mcp
    return {
        "server": {
            "name": mcp.name,
            "description": mcp.description,
            "server_name": mcp.server.name,
            "describe_all_responses": mcp._describe_all_responses,  # noqa: SLF001 — fastapi-mcp keeps its config private
            "describe_full_response_schema": mcp._describe_full_response_schema,  # noqa: SLF001 — fastapi-mcp keeps its config private
            "include_operations": mcp._include_operations,  # noqa: SLF001 — fastapi-mcp keeps its config private
            "exclude_operations": mcp._exclude_operations,  # noqa: SLF001 — fastapi-mcp keeps its config private
            "include_tags": mcp._include_tags,  # noqa: SLF001 — fastapi-mcp keeps its config private
            "exclude_tags": mcp._exclude_tags,  # noqa: SLF001 — fastapi-mcp keeps its config private
        },
        "tools": _tool_dumps(mcp.tools),
        "operation_map": {
            name: {"method": entry["method"], "path": entry["path"]} for name, entry in mcp.operation_map.items()
        },
    }


def test_mcp_surface_matches_golden(mcp_app: FastAPI) -> None:
    """Server metadata, every tool (name/description/inputSchema) and the dispatch map."""
    text = json.dumps(_surface(mcp_app), indent=1, ensure_ascii=False) + "\n"
    assert_golden("contracts/mcp_tools.json", text)


def test_mcp_tools_match_phase0_baseline_fingerprint(mcp_app: FastAPI) -> None:
    """MCP tools match phase0 baseline fingerprint."""
    tools = sorted(_tool_dumps(mcp_app.state.mcp.tools), key=lambda t: t["name"])
    blob = json.dumps(tools, sort_keys=True, indent=1)
    assert hashlib.sha256(blob.encode()).hexdigest() == PHASE0_MCP_TOOLS_SHA256


def test_tools_list_handler_returns_exactly_mcp_tools(mcp_app: FastAPI) -> None:
    """The protocol-level ``tools/list`` answer is the same list, same order."""
    mcp = mcp_app.state.mcp
    handler = mcp.server.request_handlers[mcp_types.ListToolsRequest]
    result = asyncio.run(handler(mcp_types.ListToolsRequest(method="tools/list")))
    assert _tool_dumps(result.root.tools) == _tool_dumps(mcp.tools)
    assert len(result.root.tools) == MCP_TOOL_COUNT


def test_tool_names_are_operation_ids_one_to_one(mcp_app: FastAPI) -> None:
    """Tool names are operation IDs one to one."""
    mcp = mcp_app.state.mcp
    op_ids = [op["operationId"] for item in mcp_app.openapi()["paths"].values() for op in item.values()]
    assert [t.name for t in mcp.tools] == op_ids
    assert list(mcp.operation_map) == op_ids


# --- auth gate: only what tests/server/test_mcp.py does not already pin ------


@pytest.fixture
def gated_client(clean_env: Path) -> Iterator[TestClient]:
    """A client on an app whose MCP endpoint requires the contract token."""
    del clean_env  # requested only for its side effect
    application, engine = _make_stubbed_app(bypass_auth=True, mcp_token=_TOKEN)
    install_offline_weather(application)
    yield TestClient(application, raise_server_exceptions=False)
    engine.dispose()


@pytest.fixture
def unconfigured_client(clean_env: Path) -> Iterator[TestClient]:
    """A client on an app with no MCP token configured (the endpoint fails closed)."""
    del clean_env  # requested only for its side effect
    application, engine = _make_stubbed_app(bypass_auth=True, mcp_token=None)
    install_offline_weather(application)
    yield TestClient(application, raise_server_exceptions=False)
    engine.dispose()


_BAD_HEADERS: dict[str, dict[str, str]] = {
    "missing": {},
    "wrong-token": {"Authorization": "Bearer not-the-token"},
    "basic-scheme": {"Authorization": f"Basic {_TOKEN}"},
    "empty-bearer": {"Authorization": "Bearer "},
}


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("header_case", sorted(_BAD_HEADERS))
def test_mcp_401_carries_www_authenticate_header(gated_client: TestClient, method: str, header_case: str) -> None:
    """An MCP 401 carries ``WWW-Authenticate: Bearer`` through to the client.

    ``require_mcp_token`` raises 401 with ``WWW-Authenticate: Bearer`` and the JSON branch
    of the global HTTPException handler keeps ``exc.headers``, so the client receives it.
    """
    resp = gated_client.request(method, "/mcp", headers=_BAD_HEADERS[header_case])
    assert resp.status_code == HTTPStatus.UNAUTHORIZED
    assert resp.json() == {"detail": "Invalid MCP token"}
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": f"Bearer {_TOKEN}"}])
def test_mcp_503_body_when_token_unset_on_every_method(
    unconfigured_client: TestClient, method: str, headers: dict[str, str]
) -> None:
    """MCP 503 body when token unset on every method."""
    resp = unconfigured_client.request(method, "/mcp", headers=headers)
    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert resp.json() == {"detail": "MCP auth not configured"}
    assert "www-authenticate" not in resp.headers
