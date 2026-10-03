"""Characterization: the OpenAPI document and the full route table (G1).

Pins *what the app exposes today* so a behavior-preserving refactor can prove it
changed nothing:

- ``openapi.json`` — the complete ``app.openapi()`` document, serialized in the
  order FastAPI produces it (path order = router include order, property order =
  Pydantic field order; FastAPI itself sorts ``components.schemas`` by name). Key
  order is part of the contract (MCP ``inputSchema`` property order, client
  codegen), so it is *not* re-sorted.
- ``routes.json`` — every entry in ``app.routes`` in registration order (API, web,
  well-known, docs, the ``/static`` mount and the ``/mcp`` endpoint): method set,
  path, route function name, response model, status code, tags,
  ``include_in_schema``, response class and route-level dependencies. Order is
  behavior — Starlette matches the first route that fits.

The package version (bumped by release-please on every release) is normalized to
``<VERSION>``; nothing else is normalized. ``info.version`` is the hard-coded
``"1.0.0"`` passed to ``FastAPI(...)`` and is pinned verbatim.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import typing

import pytest
from fastapi.routing import APIRoute, _iter_routes_with_context
from starlette.routing import Mount

from golden import assert_golden, assert_golden_json, install_offline_weather
from server.conftest import _make_stubbed_app

# sha256 of ``json.dumps(app.openapi(), sort_keys=True)`` recorded in
# refactor/00-contracts.md at the Phase-0 baseline (main @ a1b2622); re-recorded only by
# reviewed behavior-change commits (`fix(drift|consistency): …`) or description-only doc-contract
# commits (`docs(api): …`) that also regenerate the golden.
PHASE0_OPENAPI_SHA256 = (
    "d6750d317b8aee6de395fb48005e44ddb1cf6b1a584d4f860029c16b77f89d95"  # gitleaks:allow — content hash, not a secret
)


def _package_version() -> str:
    return importlib.metadata.version("greenhouse-server")


def _normalize_version(text: str) -> str:
    return text.replace(_package_version(), "<VERSION>")


@pytest.fixture
def contract_app(clean_env):
    """A real-auth app (no dependency overrides on the auth gate) on in-memory SQLite."""
    application, engine = _make_stubbed_app(bypass_auth=False)
    install_offline_weather(application)
    yield application
    engine.dispose()


def _type_name(tp: typing.Any) -> str | None:
    """Readable, module-free rendering of a response_model (``list[ClusterResponse]``)."""
    if tp is None:
        return None
    origin = typing.get_origin(tp)
    if origin is not None:
        args = ", ".join(_type_name(a) or "None" for a in typing.get_args(tp))
        return f"{getattr(origin, '__name__', repr(origin))}[{args}]"
    return getattr(tp, "__name__", repr(tp))


def _callable_name(fn: typing.Any) -> str:
    return getattr(fn, "__name__", type(fn).__name__)


def _route_rows(app) -> list[dict]:
    rows: list[dict] = []
    for original, context in _iter_routes_with_context(app.routes):
        route = context if context is not None else original
        if isinstance(original, APIRoute):
            response_class = route.response_class
            response_class = getattr(response_class, "value", response_class)  # unwrap DefaultPlaceholder
            rows.append(
                {
                    "kind": "APIRoute",
                    "methods": sorted(route.methods),
                    "path": route.path,
                    "name": route.name,
                    "response_model": _type_name(route.response_model),
                    "status_code": route.status_code,
                    "tags": [str(t) for t in route.tags],
                    "include_in_schema": route.include_in_schema,
                    "response_class": getattr(response_class, "__name__", repr(response_class)),
                    "dependencies": [_callable_name(d.dependency) for d in route.dependencies],
                }
            )
        elif isinstance(original, Mount):
            rows.append(
                {
                    "kind": "Mount",
                    "path": route.path,
                    "name": route.name,
                    "app": type(route.app).__name__,
                }
            )
        else:
            rows.append(
                {
                    "kind": type(route).__name__,
                    "methods": sorted(getattr(route, "methods", None) or []),
                    "path": getattr(route, "path", None),
                    "name": getattr(route, "name", None),
                    "include_in_schema": getattr(route, "include_in_schema", None),
                }
            )
    return rows


def test_openapi_document_matches_golden(contract_app):
    """The full OpenAPI document, key order preserved, equals the golden."""
    text = json.dumps(contract_app.openapi(), indent=1, ensure_ascii=False) + "\n"
    assert_golden("contracts/openapi.json", _normalize_version(text))


def test_openapi_matches_phase0_baseline_fingerprint(contract_app):
    """The document still hashes to the Phase-0 baseline recorded in 00-contracts.md."""
    digest = hashlib.sha256(json.dumps(contract_app.openapi(), sort_keys=True).encode()).hexdigest()
    assert digest == PHASE0_OPENAPI_SHA256


def test_openapi_header_fields(contract_app):
    """Title, hard-coded version, OpenAPI version and declared tag order are frozen."""
    spec = contract_app.openapi()
    assert spec["info"]["title"] == "Greenhouse API"
    assert spec["info"]["version"] == "1.0.0"
    assert spec["openapi"] == "3.1.0"
    assert [t["name"] for t in spec["tags"]] == [
        "clusters",
        "plants",
        "irrigators",
        "sensors",
        "configs",
        "operations",
        "scheduler",
        "alerts",
        "activity",
        "decisions",
        "preferences",
        "vacation",
        "search",
        "bulk",
    ]


def test_operation_id_is_route_function_name(contract_app):
    """``generate_unique_id_function=lambda r: r.name`` → operationId == function name."""
    by_key = {
        (method.upper(), path): op["operationId"]
        for path, item in contract_app.openapi()["paths"].items()
        for method, op in item.items()
    }
    names = {
        (m, row["path"]): row["name"]
        for row in _route_rows(contract_app)
        if row["kind"] == "APIRoute" and row["include_in_schema"]
        for m in row["methods"]
    }
    assert by_key == names
    assert len(by_key) == 84
    assert len(contract_app.openapi()["paths"]) == 66


def test_route_table_matches_golden(contract_app):
    """Every route in registration order (API + web + mounts + MCP) equals the golden."""
    assert_golden_json("contracts/routes.json", _route_rows(contract_app))


def test_route_table_counts(contract_app):
    """Headline counts from 00-contracts.md: 84 schema operations, 85 web routes."""
    rows = _route_rows(contract_app)
    api = [r for r in rows if r["kind"] == "APIRoute" and r["include_in_schema"]]
    hidden = [r for r in rows if r["kind"] == "APIRoute" and not r["include_in_schema"]]
    web = [r for r in hidden if not r["path"].startswith(("/.well-known", "/mcp"))]
    assert len(api) == 84
    assert len(web) == 85
    assert [r["path"] for r in rows if r["kind"] == "Mount"] == ["/static"]
    mcp_rows = [r for r in rows if r["path"] == "/mcp"]
    assert len(mcp_rows) == 1 and mcp_rows[0]["methods"] == ["DELETE", "GET", "POST"]
    assert mcp_rows[0]["dependencies"] == ["require_mcp_token"]
