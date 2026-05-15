"""HTTP-layer authentication: JWT issuance, request dependencies, bootstrap.

Password hashing lives in `greenhouse_core.auth` because it touches the User
model. Anything that needs FastAPI / `Request` / cookies lives here.

The auth model is single-user with optional multi-user expansion later:
- POST /api/v1/auth/login → JWT in JSON + HTTPOnly cookie for the web UI.
- Every /api/v1 route (except /auth/login and /.well-known/*) requires the
  JWT; the dependency reads it from either the bearer header or the cookie,
  so the same login works for API clients and browser sessions.
- Web routes use the same dependency but redirect to /login on missing/invalid
  credentials instead of returning 401 JSON.
- /mcp keeps its own bearer token (`require_mcp_token` in app.py) for the
  outer gate. fastapi-mcp then re-issues each tools/call as an inner
  /api/v1 request, forwarding the same `Authorization: Bearer <MCP_TOKEN>`
  header, so `require_user` ALSO accepts that token (as a synthetic `mcp`
  principal) — otherwise every MCP tool would 401 trying to decode the
  static token as a JWT.

When `Settings.auth_enabled` is False the dependency short-circuits to a
synthetic system user. The flag exists so a single-process restart can
recover from a lost admin password (the operator can set
IRRIGATION_AUTH_ENABLED=false, log in to the host, change the password, then
re-enable) — not as a permanent state.
"""

from __future__ import annotations

import hmac
import logging
import time
from collections.abc import Generator
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from greenhouse_core.auth import (
    create_user,
    get_user,
    get_user_by_username,
    needs_rehash,
    set_password,
    verify_password,
)
from greenhouse_core.models import User
from greenhouse_server.config import Settings

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
JWT_AUDIENCE = "greenhouse-session"
SYSTEM_USER_ID = -1
SYSTEM_USER_NAME = "system"
# MCP gets its own synthetic principal so audit logs can tell apart
# "auth disabled, falling back to system" from "real MCP tool call".
MCP_USER_ID = -2
MCP_USER_NAME = "mcp"


class AuthError(HTTPException):
    """401 with a WWW-Authenticate hint."""

    def __init__(self, detail: str = "Not authenticated"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class AuthConfigError(HTTPException):
    """503 — auth is on but misconfigured (e.g. no secret key)."""

    def __init__(self, detail: str = "Auth not configured"):
        super().__init__(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


@dataclass(frozen=True)
class AuthenticatedUser:
    """Lightweight view of the authenticated principal. Returned by `require_user`."""

    id: int
    username: str
    is_system: bool = False


# ── JWT helpers ─────────────────────────────────────────────────────────────


def _require_secret(settings: Settings) -> str:
    if not settings.auth_secret_key:
        raise AuthConfigError("auth_secret_key is not set")
    return settings.auth_secret_key


def issue_token(settings: Settings, user: User, *, now: int | None = None) -> str:
    """Mint a session JWT for `user`. Expiry comes from settings.auth_token_ttl_minutes."""
    secret = _require_secret(settings)
    iat = now if now is not None else int(time.time())
    exp = iat + settings.auth_token_ttl_minutes * 60
    payload = {
        "sub": str(user.id),
        "preferred_username": user.username,
        "iat": iat,
        "exp": exp,
        "aud": JWT_AUDIENCE,
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_token(settings: Settings, token: str) -> dict:
    """Decode and validate a session JWT. Raises AuthError on any failure."""
    secret = _require_secret(settings)
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            options={"require": ["sub", "iat", "exp", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Session expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid session") from exc


# ── FastAPI dependencies ────────────────────────────────────────────────────


_bearer = HTTPBearer(auto_error=False)


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _session_from_app(request: Request) -> Generator[Session, None, None]:
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _extract_token(
    request: Request,
    creds: HTTPAuthorizationCredentials | None,
    settings: Settings,
) -> str | None:
    """Pull a token from Authorization header first, then the session cookie."""
    if creds and creds.credentials:
        return creds.credentials
    cookie = request.cookies.get(settings.auth_cookie_name)
    return cookie or None


def _resolve_user(token: str, settings: Settings, session: Session) -> AuthenticatedUser:
    payload = decode_token(settings, token)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("Malformed session") from exc
    user = get_user(session, user_id)
    if user is None or not user.is_active:
        raise AuthError("User no longer active")
    return AuthenticatedUser(id=user.id, username=user.username)


def _is_mcp_token(token: str, settings: Settings) -> bool:
    """Constant-time check of `token` against `settings.mcp_token`.

    Returns False when the MCP token is unconfigured so a missing setting can
    never accidentally satisfy this gate.
    """
    expected = settings.mcp_token
    if not expected:
        return False
    return hmac.compare_digest(token, expected)


def require_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(_get_settings),
    session: Session = Depends(_session_from_app),
) -> AuthenticatedUser:
    """API dependency — returns the authenticated user or raises 401.

    Two authentication modes are accepted on `/api/v1` routes:

    1. The session JWT issued by `/api/v1/auth/login` (bearer header or
       session cookie). This is the human/API client path.
    2. The static MCP bearer token. `fastapi-mcp` reuses the `/api/v1` routes
       internally to fulfil `tools/call`, forwarding the inbound MCP
       `Authorization` header onto each inner request. The outer `/mcp` mount
       is already gated by `require_mcp_token`, so a request that reaches
       this dependency carrying the MCP token has already passed that gate;
       we accept it here as a synthetic `mcp` principal (id `MCP_USER_ID`)
       so audit logs can distinguish MCP-driven calls from the
       `auth_enabled=False` system fallback.

    Skips entirely when `auth_enabled` is False, returning a synthetic system
    user so route code can keep depending on the principal without
    conditional branches.
    """
    if not settings.auth_enabled:
        return AuthenticatedUser(id=SYSTEM_USER_ID, username=SYSTEM_USER_NAME, is_system=True)
    token = _extract_token(request, creds, settings)
    if not token:
        raise AuthError()
    if _is_mcp_token(token, settings):
        return AuthenticatedUser(id=MCP_USER_ID, username=MCP_USER_NAME, is_system=True)
    return _resolve_user(token, settings, session)


def require_web_user(
    request: Request,
    settings: Settings = Depends(_get_settings),
    session: Session = Depends(_session_from_app),
) -> AuthenticatedUser:
    """Web-page dependency — same as require_user but redirects to /login.

    Browsers don't carry an Authorization header, so cookies are the only
    source. Returning a 401 page in a browser is hostile; redirect instead.
    """
    if not settings.auth_enabled:
        return AuthenticatedUser(id=SYSTEM_USER_ID, username=SYSTEM_USER_NAME, is_system=True)
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        raise _redirect_to_login(request)
    try:
        return _resolve_user(token, settings, session)
    except AuthError as exc:
        raise _redirect_to_login(request) from exc


def _redirect_to_login(request: Request) -> HTTPException:
    """Build a 303 redirect that the web exception handler converts to a Response."""
    next_url = request.url.path
    if request.url.query:
        next_url += "?" + request.url.query
    return _RedirectAuthError(next_url)


class _RedirectAuthError(HTTPException):
    """Sentinel — the web exception handler catches this and serves a real redirect."""

    def __init__(self, next_url: str):
        super().__init__(status_code=status.HTTP_303_SEE_OTHER, detail="Redirect to login")
        self.next_url = next_url


def render_login_redirect(err: _RedirectAuthError) -> RedirectResponse:
    """Convert the sentinel into a 303 to /login?next=<original-path>."""
    from urllib.parse import quote

    return RedirectResponse(url=f"/login?next={quote(err.next_url)}", status_code=303)


# ── Cookie helpers ──────────────────────────────────────────────────────────


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    """Set the session JWT as an HTTPOnly cookie. Used by login endpoints."""
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_token_ttl_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=settings.auth_cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(key=settings.auth_cookie_name, path="/")


# ── Login + bootstrap helpers ───────────────────────────────────────────────


def authenticate(session: Session, username: str, password: str) -> User | None:
    """Verify credentials and return the User or None. Also re-hashes on success
    if the stored argon2 parameters are outdated."""
    user = get_user_by_username(session, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if needs_rehash(user.hashed_password):
        set_password(session, user, password)
    return user


def bootstrap_admin(engine: Engine, settings: Settings) -> None:
    """Create the admin user on first run from env vars.

    Called once during app startup after migrations. Idempotent: if any user
    already exists, returns immediately. If no user exists and the env vars
    are missing, logs a clear warning — the app will start but every API
    request will return 401 until a user is created out of band.
    """
    if not settings.auth_enabled:
        return
    from sqlalchemy.orm import Session as _Session

    session = _Session(engine)
    try:
        existing = session.query(User).first()
        if existing is not None:
            return
        username = settings.auth_admin_username
        password = settings.auth_admin_password
        if not username or not password:
            logger.warning(
                "Auth is enabled but no users exist and "
                "GREENHOUSE_AUTH_ADMIN_USERNAME / GREENHOUSE_AUTH_ADMIN_PASSWORD "
                "are not set. The API will reject every request with 401 until "
                "a user is created."
            )
            return
        create_user(session, username=username, password=password)
        session.commit()
        logger.info("Bootstrapped initial admin user %r from environment.", username)
    finally:
        session.close()


# Type alias for route signatures
AuthUserDep = AuthenticatedUser
