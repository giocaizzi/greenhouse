"""Login / logout / current-user endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from greenhouse_core.auth import record_login
from greenhouse_server.auth import (
    AuthenticatedUser,
    _get_settings,
    _session_from_app,
    authenticate,
    clear_session_cookie,
    issue_token,
    require_user,
    set_session_cookie,
)
from greenhouse_server.config import Settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class LoginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    username: str


class WhoAmIResponse(BaseModel):
    id: int
    username: str


class LogoutResponse(BaseModel):
    detail: str = "Logged out"


@router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
def login(
    body: LoginRequest,
    response: Response,
    settings: Settings = Depends(_get_settings),
    session: Session = Depends(_session_from_app),
) -> LoginResponse:
    """Exchange a username and password for a session JWT.

    The token is returned in the JSON body for API/CLI clients AND set as an
    HTTPOnly cookie for browser sessions, so the same login serves both.

    Args:
        body: username + password.

    Returns:
        JSON with access_token, token_type, expires_in (seconds), username.

    Raises:
        HTTPException 401 if credentials are invalid or the user is inactive.
        HTTPException 503 if auth is enabled but no secret key is configured.
    """
    if not settings.auth_enabled:
        # When auth is disabled, every request is already a system user. Return
        # a benign success so a CLI that always logs in keeps working.
        return LoginResponse(
            access_token="",
            token_type="bearer",
            expires_in=0,
            username=body.username,
        )
    user = authenticate(session, body.username, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = issue_token(settings, user)
    record_login(session, user)
    session.commit()
    set_session_cookie(response, settings, token)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.auth_token_ttl_minutes * 60,
        username=user.username,
    )


@router.post("/logout", response_model=LogoutResponse)
def logout(
    response: Response,
    settings: Settings = Depends(_get_settings),
    _user: AuthenticatedUser = Depends(require_user),
) -> LogoutResponse:
    """Clear the session cookie. JWT bearer tokens remain valid until expiry.

    Returns:
        Confirmation payload.

    Raises:
        HTTPException 401 when called without an active session.
    """
    clear_session_cookie(response, settings)
    return LogoutResponse()


@router.get("/me", response_model=WhoAmIResponse)
def whoami(user: AuthenticatedUser = Depends(require_user)) -> WhoAmIResponse:
    """Return the currently authenticated user.

    Returns:
        Authenticated user's id and username.

    Raises:
        HTTPException 401 if no valid session is present.
    """
    return WhoAmIResponse(id=user.id, username=user.username)
