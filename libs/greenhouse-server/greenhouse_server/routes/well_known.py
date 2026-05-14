"""OAuth metadata stubs at the .well-known paths, for MCP HTTP client compatibility.

These exist solely so that MCP HTTP clients which probe OAuth discovery (per
RFC 9728 and RFC 8414) before falling back to a configured bearer header don't
choke on FastAPI's default ``{"detail": "Not Found"}`` 404. Claude Code's
plugin-scope MCP transport is one such client; until the upstream regression
is fixed (see anthropics/claude-code#46510, #39271, #34008) servers that use
static bearer auth need to advertise "no OAuth available, use your bearer"
explicitly.

The greenhouse server uses a single static bearer token gated by
``GREENHOUSE_MCP_TOKEN``. The protected-resource metadata advertises an empty
``authorization_servers`` list so probing clients stop their OAuth flow and
apply the bearer header they were already configured with.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(include_in_schema=False)


@router.get("/.well-known/oauth-protected-resource")
def oauth_protected_resource(request: Request) -> dict[str, object]:
    """Return RFC 9728 protected-resource metadata for the /mcp endpoint.

    Advertises the MCP endpoint as a bearer-token-protected resource with no
    OAuth authorization servers, so MCP clients that probe this URL stop the
    discovery flow here and apply the static bearer header from their
    config instead of trying to negotiate a token they can't get.

    Returns:
        Dict matching the RFC 9728 metadata shape with ``resource``,
        ``authorization_servers`` (empty), ``bearer_methods_supported``, and
        ``resource_name`` fields.
    """
    base = f"{request.url.scheme}://{request.url.netloc}"
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [],
        "bearer_methods_supported": ["header"],
        "resource_name": "greenhouse",
    }


@router.get("/.well-known/oauth-authorization-server")
def oauth_authorization_server() -> JSONResponse:
    """Return an RFC 8414 authorization-server metadata stub as a JSON 404.

    Greenhouse is not an OAuth authorization server, so this returns 404 — but
    as parseable JSON, not FastAPI's default ``{"detail": "Not Found"}``. Some
    MCP clients probe this URL as a fallback when RFC 9728 doesn't satisfy
    them; if the body isn't OAuth-shaped JSON the client crashes during Zod
    validation instead of moving on (see anthropics/claude-code#34008).

    Returns:
        404 ``JSONResponse`` with ``application/json`` content-type and an
        OAuth-shaped error body so probing clients can parse and continue.
    """
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_an_authorization_server",
            "error_description": (
                "greenhouse uses static bearer auth; see "
                ".well-known/oauth-protected-resource"
            ),
        },
    )
