"""Characterization: authentication gaps not pinned by ``test_auth.py`` / ``test_mcp.py``.

Covers ``greenhouse_core.auth`` (Argon2 hashing/verify/rehash, user helpers) and
``greenhouse_server.auth`` (JWT issue/verify incl. expiry under a frozen clock,
every fail-closed branch with its exact status / detail / headers, cookie
attributes, bootstrap) plus the MCP bearer gate (503 unset, 401 missing/wrong).

Argon2 hashes used for exact comparisons are generated with a fixed salt so the
tests are deterministic; JWTs are compared by decoded claims, never verbatim.
"""

from __future__ import annotations

import hmac
import logging
from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import jwt
import pytest
from argon2 import PasswordHasher, Type
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from golden import FROZEN_INSTANT, FROZEN_TS, install_offline_weather
from greenhouse_core import auth as core_auth
from greenhouse_core.models import Base, User
from greenhouse_server import auth as server_auth
from greenhouse_server.auth import (
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    MCP_USER_ID,
    SYSTEM_USER_ID,
    AuthConfigError,
    AuthError,
    bootstrap_admin,
    decode_token,
    issue_token,
)
from greenhouse_server.config import Settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    import time_machine
    from fastapi import FastAPI
    from sqlalchemy import Engine
    from time_machine import Traveller

from .conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME, TEST_AUTH_SECRET, TEST_MCP_TOKEN, _make_stubbed_app

PW = "correct horse"
SALT = b"fixedsalt1234567"
HASH_CURRENT = PasswordHasher().hash(PW, salt=SALT)
HASH_OLD_PARAMS = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PW, salt=SALT)
HASH_ARGON2I = PasswordHasher(type=Type.I).hash(PW, salt=SALT)
MCP_TOKEN = TEST_MCP_TOKEN
TTL_SECONDS = 24 * 60 * 60


@pytest.fixture(autouse=True)
def _hermetic(clean_env: Path, frozen_clock: time_machine.Traveller) -> time_machine.Traveller:
    """No env/.env leakage into Settings, UTC, and a frozen wall clock for every test."""
    del clean_env  # requested only for its side effect
    return frozen_clock


@pytest.fixture
def db() -> Iterator[Session]:
    """A session on a fresh in-memory schema, for the core auth helpers."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "db_url": "sqlite://", "auth_secret_key": TEST_AUTH_SECRET}
    base.update(overrides)
    return Settings(**base)


def _client(**settings_override: object) -> tuple[TestClient, FastAPI, Engine]:
    app, engine = _make_stubbed_app(bypass_auth=False, **settings_override)
    install_offline_weather(app)
    return TestClient(app, raise_server_exceptions=False), app, engine


@pytest.fixture
def real() -> Iterator[tuple[TestClient, FastAPI]]:
    """A client and app with real auth (no bypass) and the contract MCP token configured."""
    client, app, engine = _client(mcp_token=MCP_TOKEN)
    yield client, app
    engine.dispose()


def _admin(app: FastAPI) -> User:
    session = app.state.session_factory()
    try:
        user = core_auth.get_user_by_username(session, TEST_ADMIN_USERNAME)
        assert user is not None
        return user
    finally:
        session.close()


# ── greenhouse_core.auth: hashing ─────────────────────────────────────────────


class TestPasswordHashing:
    """Tests for password hashing."""

    def test_hash_is_salted_argon2id_with_library_defaults(self) -> None:
        """Hash is salted argon2id with library defaults."""
        first, second = core_auth.hash_password(PW), core_auth.hash_password(PW)
        assert first.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
        assert first != second  # random salt per hash
        assert core_auth.verify_password(PW, first)
        assert core_auth.verify_password(PW, second)
        assert (
            HASH_CURRENT
            == "$argon2id$v=19$m=65536,t=3,p=4$Zml4ZWRzYWx0MTIzNDU2Nw$Tx/iK5pGB3NIurtkkFEGlw0tIWgk27e5e2pi1nsrpOE"
        )

    @pytest.mark.parametrize("plain", ["", None])
    def test_empty_password_is_rejected(self, plain: str | None) -> None:
        """Empty password is rejected."""
        with pytest.raises(ValueError, match="password must be non-empty") as excinfo:
            core_auth.hash_password(cast("str", plain))  # None on purpose: the guard rejects it
        assert str(excinfo.value) == "password must be non-empty"

    @pytest.mark.parametrize(
        ("plain", "hashed", "expected"),
        [
            (PW, HASH_CURRENT, True),
            (PW, HASH_OLD_PARAMS, True),
            (PW, HASH_ARGON2I, True),  # any argon2 variant verifies
            ("wrong", HASH_CURRENT, False),
            ("", HASH_CURRENT, False),
            (PW, "garbage", False),  # InvalidHashError
            (PW, "", False),
            (PW, HASH_CURRENT[:-10] + "AAAAAAAAAA", False),  # well-formed but wrong tag
        ],
    )
    def test_verify_password_matrix(self, plain: str, hashed: str, *, expected: bool) -> None:
        """Verify password matrix."""
        assert core_auth.verify_password(plain, hashed) is expected

    def test_verify_password_returns_false_on_truncated_hash(self) -> None:
        """A truncated stored hash verifies as False, as the docstring promises.

        argon2 raises its generic ``VerificationError("Decoding failed")`` for it, which used to
        escape and turn a login into a 500.
        """
        assert core_auth.verify_password(PW, HASH_CURRENT[:-1]) is False

    def test_verify_password_returns_false_on_a_non_ascii_corrupt_hash(self) -> None:
        """A non-ASCII byte in the stored hash raises ``UnicodeEncodeError`` inside argon2; still False."""
        assert core_auth.verify_password(PW, HASH_CURRENT[:-2] + "ü") is False

    @pytest.mark.parametrize(
        ("hashed", "expected"),
        [
            (HASH_CURRENT, False),
            (HASH_OLD_PARAMS, True),
            (HASH_ARGON2I, True),
            ("garbage", True),
            ("", True),
            (HASH_CURRENT[:-1], True),
        ],
    )
    def test_needs_rehash(self, hashed: str, *, expected: bool) -> None:
        """Needs rehash."""
        assert core_auth.needs_rehash(hashed) is expected


class TestUserHelpers:
    """Tests for user helpers."""

    def test_create_user_flushes_with_frozen_created_at(self, db: Session) -> None:
        """Create user flushes with frozen created at."""
        user = core_auth.create_user(db, "Alice", PW)
        assert user.id is not None  # flushed, not committed
        assert (user.username, user.is_active, user.created_at, user.last_login_at) == ("Alice", True, FROZEN_TS, None)
        assert core_auth.verify_password(PW, user.hashed_password)
        assert core_auth.get_user(db, user.id) is user
        assert core_auth.get_user(db, 999) is None
        assert core_auth.get_user_by_username(db, "Alice") is user
        assert core_auth.get_user_by_username(db, "alice") is None  # case-sensitive

    def test_create_user_with_empty_password_raises_before_insert(self, db: Session) -> None:
        """Create user with empty password raises before insert."""
        with pytest.raises(ValueError, match="password must be non-empty"):
            core_auth.create_user(db, "Bob", "")
        assert db.query(User).count() == 0

    def test_set_password_and_record_login(self, db: Session, frozen_clock: Traveller) -> None:
        """Set password and record login."""
        user = core_auth.create_user(db, "Carol", PW)
        core_auth.set_password(db, user, "new secret")
        assert core_auth.verify_password("new secret", user.hashed_password)
        assert not core_auth.verify_password(PW, user.hashed_password)
        frozen_clock.move_to(FROZEN_INSTANT + timedelta(seconds=90))
        core_auth.record_login(db, user)
        assert user.last_login_at == FROZEN_TS + 90


# ── greenhouse_server.auth: JWT ───────────────────────────────────────────────


class _U:
    def __init__(self, uid: int = 5, username: str = "dave") -> None:
        self.id = uid
        self.username = username


def _user() -> User:
    """A stand-in user: ``issue_token`` reads only ``id`` and ``username``."""
    return cast("User", _U())


class TestJwt:
    """Tests for jwt."""

    def test_issue_token_claims_and_header(self) -> None:
        """Issue token claims and header."""
        token = issue_token(_settings(), _user())
        assert jwt.get_unverified_header(token) == {"alg": "HS256", "typ": "JWT"}
        assert jwt.decode(token, options={"verify_signature": False}) == {
            "sub": "5",
            "preferred_username": "dave",
            "iat": FROZEN_TS,
            "exp": FROZEN_TS + TTL_SECONDS,
            "aud": "greenhouse-session",
        }
        assert (JWT_ALGORITHM, JWT_AUDIENCE) == ("HS256", "greenhouse-session")

    def test_issue_token_honours_now_and_ttl(self) -> None:
        """Issue token honours now and TTL."""
        token = issue_token(_settings(auth_token_ttl_minutes=1), _user(), now=1000)
        claims = jwt.decode(token, options={"verify_signature": False})
        assert (claims["iat"], claims["exp"]) == (1000, 1060)

    def test_decode_roundtrip_and_expiry_boundary(self, frozen_clock: Traveller) -> None:
        """Decode roundtrip and expiry boundary."""
        settings = _settings(auth_token_ttl_minutes=1)
        token = issue_token(settings, _user())
        frozen_clock.move_to(FROZEN_INSTANT + timedelta(seconds=59))
        assert decode_token(settings, token)["sub"] == "5"
        frozen_clock.move_to(FROZEN_INSTANT + timedelta(seconds=60))  # now == exp → expired
        with pytest.raises(AuthError) as excinfo:
            decode_token(settings, token)
        assert (excinfo.value.status_code, excinfo.value.detail, excinfo.value.headers) == (
            401,
            "Session expired",
            {"WWW-Authenticate": "Bearer"},
        )

    @pytest.mark.parametrize(
        "make_token",
        [
            lambda: "not-a-jwt",
            lambda: jwt.encode({"sub": "5", "iat": FROZEN_TS, "exp": FROZEN_TS + 60, "aud": "other"}, TEST_AUTH_SECRET),
            lambda: jwt.encode({"sub": "5", "iat": FROZEN_TS, "exp": FROZEN_TS + 60}, TEST_AUTH_SECRET),
            lambda: jwt.encode({"sub": "5", "exp": FROZEN_TS + 60, "aud": JWT_AUDIENCE}, TEST_AUTH_SECRET),
            lambda: jwt.encode({"iat": FROZEN_TS, "exp": FROZEN_TS + 60, "aud": JWT_AUDIENCE}, TEST_AUTH_SECRET),
            lambda: jwt.encode(
                {"sub": "5", "iat": FROZEN_TS, "exp": FROZEN_TS + 60, "aud": JWT_AUDIENCE}, "wrong-secret-x" * 3
            ),
            lambda: jwt.encode(
                {"sub": "5", "iat": FROZEN_TS, "exp": FROZEN_TS + 60, "aud": JWT_AUDIENCE},
                TEST_AUTH_SECRET,
                algorithm="HS512",
            ),
            lambda: jwt.encode(
                # alg "none" takes no key; jwt's stubs demand one, so the deliberate None is cast
                {"sub": "5", "iat": FROZEN_TS, "exp": FROZEN_TS + 60, "aud": JWT_AUDIENCE},
                cast("str", None),
                algorithm="none",
            ),
        ],
        ids=["garbage", "wrong-aud", "no-aud", "no-iat", "no-sub", "wrong-secret", "hs512", "alg-none"],
    )
    def test_every_other_failure_is_invalid_session(self, make_token: Callable[[], str]) -> None:
        """Every other failure is invalid session."""
        with pytest.raises(AuthError) as excinfo:
            decode_token(_settings(), make_token())
        assert (excinfo.value.status_code, excinfo.value.detail) == (401, "Invalid session")

    @pytest.mark.parametrize("secret", [None, ""])
    def test_missing_secret_is_503_for_issue_and_decode(self, secret: str | None) -> None:
        """Missing secret is 503 for issue and decode."""
        settings = _settings(auth_secret_key=secret)
        for call in (lambda: issue_token(settings, _user()), lambda: decode_token(settings, "x")):
            with pytest.raises(AuthConfigError) as excinfo:
                call()
            assert (excinfo.value.status_code, excinfo.value.detail, excinfo.value.headers) == (
                503,
                "auth_secret_key is not set",
                None,
            )

    def test_error_class_defaults(self) -> None:
        """Error class defaults."""
        assert (AuthError().status_code, AuthError().detail) == (401, "Not authenticated")
        assert (AuthConfigError().status_code, AuthConfigError().detail) == (503, "Auth not configured")


# ── /api/v1 gate over HTTP ────────────────────────────────────────────────────


def _token(app: FastAPI, **claims: object) -> str:
    payload: dict[str, object] = {
        "sub": str(_admin(app).id),
        "iat": FROZEN_TS,
        "exp": FROZEN_TS + 60,
        "aud": JWT_AUDIENCE,
    }
    payload.update(claims)
    return jwt.encode({k: v for k, v in payload.items() if v is not None}, TEST_AUTH_SECRET, algorithm="HS256")


@pytest.mark.parametrize(
    ("auth", "status", "body"),
    [
        (lambda _app: {}, 401, {"detail": "Not authenticated"}),
        (lambda _app: {"headers": {"Authorization": "Bearer junk"}}, 401, {"detail": "Invalid session"}),
        (lambda _app: {"headers": {"Authorization": "Basic Zm9vOmJhcg=="}}, 401, {"detail": "Not authenticated"}),
        (
            lambda app: {"headers": {"Authorization": f"Bearer {_token(app, exp=FROZEN_TS)}"}},
            401,
            {"detail": "Session expired"},
        ),
        (
            lambda app: {"headers": {"Authorization": f"Bearer {_token(app, sub='abc')}"}},
            401,
            {"detail": "Malformed session"},
        ),
        (
            lambda app: {"headers": {"Authorization": f"Bearer {_token(app, sub='999')}"}},
            401,
            {"detail": "User no longer active"},
        ),
        (lambda app: {"cookies": {"greenhouse_session": _token(app)}}, 200, "admin"),
        (lambda app: {"headers": {"Authorization": f"Bearer {_token(app)}"}}, 200, "admin"),
        (
            lambda app: {"headers": {"Authorization": "Bearer junk"}, "cookies": {"greenhouse_session": _token(app)}},
            401,
            {"detail": "Invalid session"},  # the header wins over a valid cookie
        ),
        (
            lambda _app: {"headers": {"Authorization": f"Bearer {MCP_TOKEN}"}},
            200,
            {"id": MCP_USER_ID, "username": "mcp"},
        ),
        # The MCP token is bearer-only: in the cookie it is decoded as a JWT and refused.
        (lambda _app: {"cookies": {"greenhouse_session": MCP_TOKEN}}, 401, {"detail": "Invalid session"}),
    ],
    ids=[
        "no-credentials",
        "garbage-bearer",
        "non-bearer-scheme",
        "expired",
        "non-int-sub",
        "deleted-user",
        "cookie-only",
        "bearer",
        "bad-header-good-cookie",
        "mcp-token-bearer",
        "mcp-token-cookie",
    ],
)
def test_require_user_matrix(
    real: tuple[TestClient, FastAPI],
    auth: Callable[[FastAPI], dict[str, Any]],
    status: int,
    body: dict[str, object] | str,
) -> None:
    """Require user matrix."""
    client, app = real
    kwargs = auth(app)
    client.cookies.clear()
    for name, value in kwargs.pop("cookies", {}).items():
        client.cookies.set(name, value)
    resp = client.get("/api/v1/auth/me", **kwargs)
    assert resp.status_code == status
    if body == "admin":
        body = {"id": _admin(app).id, "username": TEST_ADMIN_USERNAME}
    assert resp.json() == body


@pytest.mark.parametrize("corrupt", [lambda h: h[:-1], lambda h: h[:-2] + "ü"], ids=["truncated", "non-ascii"])
def test_login_with_a_corrupt_stored_hash_is_401(
    real: tuple[TestClient, FastAPI], corrupt: Callable[[str], str]
) -> None:
    """A corrupted password row refuses the login like a wrong password (401), not a 500."""
    client, app = real
    session = app.state.session_factory()
    admin = core_auth.get_user_by_username(session, TEST_ADMIN_USERNAME)
    assert admin is not None
    admin.hashed_password = corrupt(admin.hashed_password)
    session.commit()
    session.close()
    login = client.post("/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    assert (login.status_code, login.json()) == (401, {"detail": "Invalid username or password"})


@pytest.mark.parametrize("username", ["nobody", TEST_ADMIN_USERNAME])
def test_login_runs_one_argon2_verify_whether_or_not_the_user_exists(
    real: tuple[TestClient, FastAPI], monkeypatch: pytest.MonkeyPatch, username: str
) -> None:
    """An unknown username costs the same argon2 verify as a wrong password (no timing oracle)."""
    calls: list[str] = []

    def counting_verify(p: str, h: str) -> bool:
        calls.append(h)
        return core_auth.verify_password(p, h)

    monkeypatch.setattr(server_auth, "verify_password", counting_verify)
    client, _ = real
    login = client.post("/api/v1/auth/login", json={"username": username, "password": "wrong"})
    assert (login.status_code, login.json()) == (401, {"detail": "Invalid username or password"})
    assert len(calls) == 1
    assert calls[0].startswith("$argon2id$")


def test_inactive_user_is_rejected(real: tuple[TestClient, FastAPI]) -> None:
    """Inactive user is rejected."""
    client, app = real
    session = app.state.session_factory()
    admin = core_auth.get_user_by_username(session, TEST_ADMIN_USERNAME)
    assert admin is not None
    admin.is_active = False
    session.commit()
    session.close()
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {_token(app)}"})
    assert (resp.status_code, resp.json()) == (401, {"detail": "User no longer active"})
    login = client.post("/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    assert (login.status_code, login.json()) == (401, {"detail": "Invalid username or password"})


def test_api_401_carries_www_authenticate_header(real: tuple[TestClient, FastAPI]) -> None:
    """Every API 401 carries ``WWW-Authenticate: Bearer``.

    ``AuthError``, the login 401 and the MCP gate all set ``WWW-Authenticate: Bearer``, and the
    JSON error handler in ``web/exception_handlers.py`` passes ``exc.headers`` through.
    """
    client, _ = real
    responses = [
        client.get("/api/v1/clusters"),
        client.post("/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": "nope"}),
        client.get("/mcp"),
    ]
    assert [r.status_code for r in responses] == [401, 401, 401]
    assert [r.headers.get("www-authenticate") for r in responses] == ["Bearer", "Bearer", "Bearer"]


@pytest.mark.usefixtures("clean_env")
def test_secret_unset_bearer_is_503_but_no_credentials_is_401() -> None:
    """Secret unset bearer is 503 but no credentials is 401."""
    client, _, engine = _client(auth_secret_key=None)
    try:
        assert (client.get("/api/v1/auth/me").status_code, client.get("/api/v1/auth/me").json()) == (
            401,
            {"detail": "Not authenticated"},
        )
        resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer anything"})
        assert (resp.status_code, resp.json()) == (503, {"detail": "auth_secret_key is not set"})
        login = client.post(
            "/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD}
        )
        assert (login.status_code, login.json()) == (503, {"detail": "auth_secret_key is not set"})
    finally:
        engine.dispose()


@pytest.mark.usefixtures("clean_env")
def test_mcp_token_not_accepted_on_api_when_mcp_is_unconfigured() -> None:
    """MCP token not accepted on API when MCP is unconfigured."""
    client, _, engine = _client(mcp_token=None)
    try:
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {MCP_TOKEN}"})
        assert (resp.status_code, resp.json()) == (401, {"detail": "Invalid session"})
    finally:
        engine.dispose()


@pytest.mark.usefixtures("clean_env")
def test_auth_disabled_principal_is_system() -> None:
    """Auth disabled principal is system."""
    client, _, engine = _client(auth_enabled=False, auth_secret_key=None)
    try:
        assert client.get("/api/v1/auth/me").json() == {"id": SYSTEM_USER_ID, "username": "system"}
        login = client.post("/api/v1/auth/login", json={"username": "anyone", "password": "x"})
        assert (login.status_code, login.json()) == (
            200,
            {"access_token": "", "token_type": "bearer", "expires_in": 0, "username": "anyone"},
        )
        assert "set-cookie" not in login.headers
    finally:
        engine.dispose()


# ── login / logout ────────────────────────────────────────────────────────────


def test_login_success_body_cookie_and_last_login(real: tuple[TestClient, FastAPI]) -> None:
    """Login success body cookie and last login."""
    client, app = real
    resp = client.post("/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    token = body.pop("access_token")
    assert body == {"token_type": "bearer", "expires_in": TTL_SECONDS, "username": TEST_ADMIN_USERNAME}
    assert jwt.decode(token, options={"verify_signature": False}) == {
        "sub": str(_admin(app).id),
        "preferred_username": TEST_ADMIN_USERNAME,
        "iat": FROZEN_TS,
        "exp": FROZEN_TS + TTL_SECONDS,
        "aud": JWT_AUDIENCE,
    }
    assert resp.headers["set-cookie"] == (
        f"greenhouse_session={token}; HttpOnly; Max-Age={TTL_SECONDS}; Path=/; SameSite=lax"
    )
    assert _admin(app).last_login_at == FROZEN_TS


@pytest.mark.usefixtures("clean_env")
def test_login_cookie_secure_flag_and_custom_name() -> None:
    """Login cookie secure flag and custom name."""
    ttl_minutes = 5
    client, _, engine = _client(auth_cookie_secure=True, auth_cookie_name="gh", auth_token_ttl_minutes=ttl_minutes)
    try:
        resp = client.post(
            "/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD}
        )
        token = resp.json()["access_token"]
        assert resp.json()["expires_in"] == ttl_minutes * 60
        assert resp.headers["set-cookie"] == f"gh={token}; HttpOnly; Max-Age=300; Path=/; SameSite=lax; Secure"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"username": TEST_ADMIN_USERNAME, "password": "wrong"}, 401),
        ({"username": TEST_ADMIN_USERNAME.upper(), "password": TEST_ADMIN_PASSWORD}, 401),
        ({"username": "", "password": "x"}, 422),
        ({"username": "u", "password": ""}, 422),
        ({"username": "u" * 129, "password": "x"}, 422),
        ({"username": "u", "password": "p" * 513}, 422),
    ],
)
def test_login_rejections(real: tuple[TestClient, FastAPI], body: dict[str, str], status: int) -> None:
    """Login rejections."""
    client, _ = real
    resp = client.post("/api/v1/auth/login", json=body)
    assert resp.status_code == status
    if status == HTTPStatus.UNAUTHORIZED:
        assert resp.json() == {"detail": "Invalid username or password"}
        assert "set-cookie" not in resp.headers


def test_login_rehashes_outdated_hash(real: tuple[TestClient, FastAPI]) -> None:
    """Login rehashes outdated hash."""
    client, app = real
    session = app.state.session_factory()
    admin = core_auth.get_user_by_username(session, TEST_ADMIN_USERNAME)
    assert admin is not None
    admin.hashed_password = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(TEST_ADMIN_PASSWORD)
    old = admin.hashed_password
    session.commit()
    session.close()
    resp = client.post("/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    assert resp.status_code == HTTPStatus.OK
    new = _admin(app).hashed_password
    assert new != old
    assert new.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_logout_requires_auth_and_deletes_cookie(real: tuple[TestClient, FastAPI]) -> None:
    """Logout requires auth and deletes cookie."""
    client, app = real
    assert client.post("/api/v1/auth/logout").status_code == HTTPStatus.UNAUTHORIZED
    resp = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {_token(app)}"})
    assert (resp.status_code, resp.json()) == (200, {"detail": "Logged out"})
    assert resp.headers["set-cookie"] == (
        'greenhouse_session=""; expires=Wed, 15 Apr 2026 10:00:00 GMT; Max-Age=0; Path=/; SameSite=lax'
    )


# ── web redirect gate ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("cookie", [None, "junk", "expired"], ids=["no-cookie", "invalid-cookie", "expired-cookie"])
def test_web_gate_redirects_with_quoted_next(real: tuple[TestClient, FastAPI], cookie: str | None) -> None:
    """Web gate redirects with quoted next."""
    client, app = real
    client.cookies.clear()
    if cookie == "expired":
        client.cookies.set("greenhouse_session", _token(app, exp=FROZEN_TS))
    elif cookie:
        client.cookies.set("greenhouse_session", cookie)
    resp = client.get("/clusters?a=1&b=x y", follow_redirects=False)
    assert resp.status_code == HTTPStatus.SEE_OTHER
    assert resp.headers["location"] == "/login?next=/clusters%3Fa%3D1%26b%3Dx%2520y"
    hx = client.get("/clusters", headers={"HX-Request": "TRUE"}, follow_redirects=False)
    assert (hx.status_code, hx.headers["HX-Redirect"], hx.content) == (204, "/login?next=/clusters", b"")


def test_web_gate_ignores_bearer_header(real: tuple[TestClient, FastAPI]) -> None:
    """Web gate ignores bearer header."""
    client, app = real
    client.cookies.clear()
    resp = client.get("/clusters", headers={"Authorization": f"Bearer {_token(app)}"}, follow_redirects=False)
    assert resp.status_code == HTTPStatus.SEE_OTHER


# ── bootstrap_admin ───────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Iterator[Engine]:
    """A fresh in-memory engine with the schema, for bootstrap tests."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _users(engine: Engine) -> list[tuple[str, int, bool]]:
    with Session(engine) as session:
        return [(u.username, u.created_at, u.is_active) for u in session.query(User).order_by(User.id)]


def test_bootstrap_creates_admin_once(engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    """Bootstrap creates admin once."""
    settings = _settings(auth_admin_username="root", auth_admin_password=TEST_ADMIN_PASSWORD)
    with caplog.at_level(logging.INFO, logger="greenhouse_server.auth"):
        bootstrap_admin(engine, settings)
        bootstrap_admin(
            engine, _settings(auth_admin_username="other", auth_admin_password=f"{TEST_ADMIN_PASSWORD}-other")
        )
    assert _users(engine) == [("root", FROZEN_TS, True)]
    assert caplog.messages == ["Bootstrapped initial admin user 'root' from environment."]


@pytest.mark.parametrize(
    ("username", "password"),
    [(None, "pw"), ("root", None), ("", "pw"), ("root", "")],
    ids=["no-user", "no-pw", "empty-user", "empty-pw"],
)
def test_bootstrap_without_credentials_warns(
    engine: Engine, caplog: pytest.LogCaptureFixture, username: str | None, password: str | None
) -> None:
    """Bootstrap without credentials warns."""
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.auth"):
        bootstrap_admin(engine, _settings(auth_admin_username=username, auth_admin_password=password))
    assert _users(engine) == []
    assert caplog.messages == [
        "Auth is enabled but no users exist and GREENHOUSE_AUTH_ADMIN_USERNAME / GREENHOUSE_AUTH_ADMIN_PASSWORD "
        "are not set. The API will reject every request with 401 until a user is created."
    ]


def test_bootstrap_is_skipped_when_auth_disabled(engine: Engine) -> None:
    """Bootstrap is skipped when auth disabled."""
    bootstrap_admin(
        engine, _settings(auth_enabled=False, auth_admin_username="root", auth_admin_password=TEST_ADMIN_PASSWORD)
    )
    assert _users(engine) == []


# ── MCP bearer gate ───────────────────────────────────────────────────────────


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(
    ("mcp_token", "headers", "status", "detail"),
    [
        (None, {}, 503, "MCP auth not configured"),
        (None, {"Authorization": f"Bearer {MCP_TOKEN}"}, 503, "MCP auth not configured"),
        (MCP_TOKEN, {}, 401, "Invalid MCP token"),
        (MCP_TOKEN, {"Authorization": "Bearer wrong"}, 401, "Invalid MCP token"),
        (MCP_TOKEN, {"Authorization": f"Basic {MCP_TOKEN}"}, 401, "Invalid MCP token"),
        (MCP_TOKEN, {"Authorization": f"Bearer {MCP_TOKEN} "}, None, None),  # header value is stripped
        (MCP_TOKEN, {"Authorization": f"bearer {MCP_TOKEN}"}, None, None),  # scheme is case-insensitive
        (MCP_TOKEN, {"Authorization": f"Bearer {MCP_TOKEN}"}, None, None),
        ("", {"Authorization": "Bearer "}, 503, "MCP auth not configured"),  # empty token = unset
        ("", {"Authorization": "Bearer x"}, 503, "MCP auth not configured"),
        ("   ", {"Authorization": "Bearer x"}, 503, "MCP auth not configured"),  # whitespace-only = unset
    ],
)
def test_mcp_gate(mcp_token: str | None, headers: dict[str, str], status: int | None, detail: str | None) -> None:
    """MCP gate."""
    client, _, engine = _client(mcp_token=mcp_token)
    try:
        resp = client.get("/mcp", headers=headers)
        if status is None:
            assert resp.status_code not in (HTTPStatus.UNAUTHORIZED, HTTPStatus.SERVICE_UNAVAILABLE)
        else:
            assert (resp.status_code, resp.json()) == (status, {"detail": detail})
    finally:
        engine.dispose()


@pytest.mark.usefixtures("clean_env")
def test_mcp_gate_compares_the_token_in_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """B-12: the `/mcp` gate compares the bearer with ``hmac.compare_digest``, not ``!=``.

    A short-circuiting string comparison leaks how many leading characters of a guess
    match the token through response timing; the gate must use the same constant-time
    primitive as the API's MCP-principal check.
    """
    calls: list[tuple[bytes, bytes]] = []
    real_compare = hmac.compare_digest

    def spy(given: bytes, expected: bytes) -> bool:
        calls.append((given, expected))
        return real_compare(given, expected)

    monkeypatch.setattr(hmac, "compare_digest", spy)  # the module the gate calls
    client, _, engine = _client(mcp_token=MCP_TOKEN)
    try:
        assert client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == HTTPStatus.UNAUTHORIZED
        assert calls == [(b"wrong", MCP_TOKEN.encode())]
    finally:
        engine.dispose()
