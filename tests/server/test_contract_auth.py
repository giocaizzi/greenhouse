"""Characterization: authentication gaps not pinned by ``test_auth.py`` / ``test_mcp.py``.

Covers ``greenhouse_core.auth`` (Argon2 hashing/verify/rehash, user helpers) and
``greenhouse_server.auth`` (JWT issue/verify incl. expiry under a frozen clock,
every fail-closed branch with its exact status / detail / headers, cookie
attributes, bootstrap) plus the MCP bearer gate (503 unset, 401 missing/wrong).

Argon2 hashes used for exact comparisons are generated with a fixed salt so the
tests are deterministic; JWTs are compared by decoded claims, never verbatim.
Two observed quirks are pinned as current behavior (see the report):
``verify_password`` raises on a truncated hash, and the JSON error handler drops
the ``WWW-Authenticate`` header that ``AuthError`` / the MCP gate set.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import jwt
import pytest
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from golden import FROZEN_INSTANT, FROZEN_TS, install_offline_weather
from greenhouse_core import auth as core_auth
from greenhouse_core.models import Base, User
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

from .conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME, TEST_AUTH_SECRET, _make_stubbed_app

PW = "correct horse"
SALT = b"fixedsalt1234567"
HASH_CURRENT = PasswordHasher().hash(PW, salt=SALT)
HASH_OLD_PARAMS = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PW, salt=SALT)
HASH_ARGON2I = PasswordHasher(type=Type.I).hash(PW, salt=SALT)
MCP_TOKEN = "contract-mcp-token-0123456789abcdef"
TTL_SECONDS = 24 * 60 * 60


@pytest.fixture(autouse=True)
def _hermetic(clean_env, frozen_clock):
    """No env/.env leakage into Settings, UTC, and a frozen wall clock for every test."""
    yield frozen_clock


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def _settings(**overrides) -> Settings:
    base = {"_env_file": None, "db_url": "sqlite://", "auth_secret_key": TEST_AUTH_SECRET}
    base.update(overrides)
    return Settings(**base)


def _client(**settings_override):
    app, engine = _make_stubbed_app(bypass_auth=False, **settings_override)
    install_offline_weather(app)
    return TestClient(app, raise_server_exceptions=False), app, engine


@pytest.fixture
def real():
    client, app, engine = _client(mcp_token=MCP_TOKEN)
    yield client, app
    engine.dispose()


def _admin(app) -> User:
    session = app.state.session_factory()
    try:
        return core_auth.get_user_by_username(session, TEST_ADMIN_USERNAME)
    finally:
        session.close()


# ── greenhouse_core.auth: hashing ─────────────────────────────────────────────


class TestPasswordHashing:
    def test_hash_is_salted_argon2id_with_library_defaults(self):
        first, second = core_auth.hash_password(PW), core_auth.hash_password(PW)
        assert first.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
        assert first != second  # random salt per hash
        assert core_auth.verify_password(PW, first) and core_auth.verify_password(PW, second)
        assert (
            HASH_CURRENT
            == "$argon2id$v=19$m=65536,t=3,p=4$Zml4ZWRzYWx0MTIzNDU2Nw$Tx/iK5pGB3NIurtkkFEGlw0tIWgk27e5e2pi1nsrpOE"
        )

    @pytest.mark.parametrize("plain", ["", None])
    def test_empty_password_is_rejected(self, plain):
        with pytest.raises(ValueError) as excinfo:
            core_auth.hash_password(plain)
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
    def test_verify_password_matrix(self, plain, hashed, expected):
        assert core_auth.verify_password(plain, hashed) is expected

    def test_verify_password_current_behavior_raises_on_truncated_hash(self):
        """Pins current (buggy) behavior: a truncated stored hash raises instead of returning False — see REFACTOR_NOTES.md.

        The docstring promises "False on mismatch or malformed hash", but only
        ``VerifyMismatchError``/``InvalidHashError`` are caught; argon2's generic
        ``VerificationError("Decoding failed")`` escapes (a corrupted row would
        turn a login into a 500).
        """
        with pytest.raises(VerificationError, match="Decoding failed"):
            core_auth.verify_password(PW, HASH_CURRENT[:-1])

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
    def test_needs_rehash(self, hashed, expected):
        assert core_auth.needs_rehash(hashed) is expected


class TestUserHelpers:
    def test_create_user_flushes_with_frozen_created_at(self, db):
        user = core_auth.create_user(db, "Alice", PW)
        assert user.id is not None  # flushed, not committed
        assert (user.username, user.is_active, user.created_at, user.last_login_at) == ("Alice", True, FROZEN_TS, None)
        assert core_auth.verify_password(PW, user.hashed_password)
        assert core_auth.get_user(db, user.id) is user
        assert core_auth.get_user(db, 999) is None
        assert core_auth.get_user_by_username(db, "Alice") is user
        assert core_auth.get_user_by_username(db, "alice") is None  # case-sensitive

    def test_create_user_with_empty_password_raises_before_insert(self, db):
        with pytest.raises(ValueError):
            core_auth.create_user(db, "Bob", "")
        assert db.query(User).count() == 0

    def test_set_password_and_record_login(self, db, _hermetic):
        user = core_auth.create_user(db, "Carol", PW)
        core_auth.set_password(db, user, "new secret")
        assert core_auth.verify_password("new secret", user.hashed_password)
        assert not core_auth.verify_password(PW, user.hashed_password)
        _hermetic.move_to(FROZEN_INSTANT + timedelta(seconds=90))
        core_auth.record_login(db, user)
        assert user.last_login_at == FROZEN_TS + 90


# ── greenhouse_server.auth: JWT ───────────────────────────────────────────────


class _U:
    def __init__(self, uid=5, username="dave"):
        self.id = uid
        self.username = username


class TestJwt:
    def test_issue_token_claims_and_header(self):
        token = issue_token(_settings(), _U())
        assert jwt.get_unverified_header(token) == {"alg": "HS256", "typ": "JWT"}
        assert jwt.decode(token, options={"verify_signature": False}) == {
            "sub": "5",
            "preferred_username": "dave",
            "iat": FROZEN_TS,
            "exp": FROZEN_TS + TTL_SECONDS,
            "aud": "greenhouse-session",
        }
        assert (JWT_ALGORITHM, JWT_AUDIENCE) == ("HS256", "greenhouse-session")

    def test_issue_token_honours_now_and_ttl(self):
        token = issue_token(_settings(auth_token_ttl_minutes=1), _U(), now=1000)
        claims = jwt.decode(token, options={"verify_signature": False})
        assert (claims["iat"], claims["exp"]) == (1000, 1060)

    def test_decode_roundtrip_and_expiry_boundary(self, _hermetic):
        settings = _settings(auth_token_ttl_minutes=1)
        token = issue_token(settings, _U())
        _hermetic.move_to(FROZEN_INSTANT + timedelta(seconds=59))
        assert decode_token(settings, token)["sub"] == "5"
        _hermetic.move_to(FROZEN_INSTANT + timedelta(seconds=60))  # now == exp → expired
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
                {"sub": "5", "iat": FROZEN_TS, "exp": FROZEN_TS + 60, "aud": JWT_AUDIENCE}, None, algorithm="none"
            ),
        ],
        ids=["garbage", "wrong-aud", "no-aud", "no-iat", "no-sub", "wrong-secret", "hs512", "alg-none"],
    )
    def test_every_other_failure_is_invalid_session(self, make_token):
        with pytest.raises(AuthError) as excinfo:
            decode_token(_settings(), make_token())
        assert (excinfo.value.status_code, excinfo.value.detail) == (401, "Invalid session")

    @pytest.mark.parametrize("secret", [None, ""])
    def test_missing_secret_is_503_for_issue_and_decode(self, secret):
        settings = _settings(auth_secret_key=secret)
        for call in (lambda: issue_token(settings, _U()), lambda: decode_token(settings, "x")):
            with pytest.raises(AuthConfigError) as excinfo:
                call()
            assert (excinfo.value.status_code, excinfo.value.detail, excinfo.value.headers) == (
                503,
                "auth_secret_key is not set",
                None,
            )

    def test_error_class_defaults(self):
        assert (AuthError().status_code, AuthError().detail) == (401, "Not authenticated")
        assert (AuthConfigError().status_code, AuthConfigError().detail) == (503, "Auth not configured")


# ── /api/v1 gate over HTTP ────────────────────────────────────────────────────


def _token(app, **claims) -> str:
    payload = {"sub": str(_admin(app).id), "iat": FROZEN_TS, "exp": FROZEN_TS + 60, "aud": JWT_AUDIENCE}
    payload.update(claims)
    return jwt.encode({k: v for k, v in payload.items() if v is not None}, TEST_AUTH_SECRET, algorithm="HS256")


@pytest.mark.parametrize(
    ("auth", "status", "body"),
    [
        (lambda app: {}, 401, {"detail": "Not authenticated"}),
        (lambda app: {"headers": {"Authorization": "Bearer junk"}}, 401, {"detail": "Invalid session"}),
        (lambda app: {"headers": {"Authorization": "Basic Zm9vOmJhcg=="}}, 401, {"detail": "Not authenticated"}),
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
            lambda app: {"headers": {"Authorization": f"Bearer {MCP_TOKEN}"}},
            200,
            {"id": MCP_USER_ID, "username": "mcp"},
        ),
        (lambda app: {"cookies": {"greenhouse_session": MCP_TOKEN}}, 200, {"id": MCP_USER_ID, "username": "mcp"}),
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
def test_require_user_matrix(real, auth, status, body):
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


def test_inactive_user_is_rejected(real):
    client, app = real
    session = app.state.session_factory()
    admin = core_auth.get_user_by_username(session, TEST_ADMIN_USERNAME)
    admin.is_active = False
    session.commit()
    session.close()
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {_token(app)}"})
    assert (resp.status_code, resp.json()) == (401, {"detail": "User no longer active"})
    login = client.post("/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    assert (login.status_code, login.json()) == (401, {"detail": "Invalid username or password"})


def test_api_401_current_behavior_drops_www_authenticate_header(real):
    """Pins current (buggy) behavior: the JSON error handler drops ``exc.headers`` — see REFACTOR_NOTES.md.

    ``AuthError``, the login 401 and the MCP gate all set ``WWW-Authenticate: Bearer``
    but ``web/exception_handlers.py`` rebuilds a bare ``JSONResponse``, so the
    client never receives it.
    """
    client, _ = real
    responses = [
        client.get("/api/v1/clusters"),
        client.post("/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": "nope"}),
        client.get("/mcp"),
    ]
    assert [r.status_code for r in responses] == [401, 401, 401]
    assert [r.headers.get("www-authenticate") for r in responses] == [None, None, None]


def test_secret_unset_bearer_is_503_but_no_credentials_is_401(clean_env):
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


def test_mcp_token_not_accepted_on_api_when_mcp_is_unconfigured(clean_env):
    client, _, engine = _client(mcp_token=None)
    try:
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {MCP_TOKEN}"})
        assert (resp.status_code, resp.json()) == (401, {"detail": "Invalid session"})
    finally:
        engine.dispose()


def test_auth_disabled_principal_is_system(clean_env):
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


def test_login_success_body_cookie_and_last_login(real):
    client, app = real
    resp = client.post("/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    assert resp.status_code == 200
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


def test_login_cookie_secure_flag_and_custom_name(clean_env):
    client, _, engine = _client(auth_cookie_secure=True, auth_cookie_name="gh", auth_token_ttl_minutes=5)
    try:
        resp = client.post(
            "/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD}
        )
        token = resp.json()["access_token"]
        assert resp.json()["expires_in"] == 300
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
def test_login_rejections(real, body, status):
    client, _ = real
    resp = client.post("/api/v1/auth/login", json=body)
    assert resp.status_code == status
    if status == 401:
        assert resp.json() == {"detail": "Invalid username or password"}
        assert "set-cookie" not in resp.headers


def test_login_rehashes_outdated_hash(real):
    client, app = real
    session = app.state.session_factory()
    admin = core_auth.get_user_by_username(session, TEST_ADMIN_USERNAME)
    admin.hashed_password = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(TEST_ADMIN_PASSWORD)
    old = admin.hashed_password
    session.commit()
    session.close()
    resp = client.post("/api/v1/auth/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    assert resp.status_code == 200
    new = _admin(app).hashed_password
    assert new != old and new.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_logout_requires_auth_and_deletes_cookie(real):
    client, app = real
    assert client.post("/api/v1/auth/logout").status_code == 401
    resp = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {_token(app)}"})
    assert (resp.status_code, resp.json()) == (200, {"detail": "Logged out"})
    assert resp.headers["set-cookie"] == (
        'greenhouse_session=""; expires=Wed, 15 Apr 2026 10:00:00 GMT; Max-Age=0; Path=/; SameSite=lax'
    )


# ── web redirect gate ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("cookie", [None, "junk", "expired"], ids=["no-cookie", "invalid-cookie", "expired-cookie"])
def test_web_gate_redirects_with_quoted_next(real, cookie):
    client, app = real
    client.cookies.clear()
    if cookie == "expired":
        client.cookies.set("greenhouse_session", _token(app, exp=FROZEN_TS))
    elif cookie:
        client.cookies.set("greenhouse_session", cookie)
    resp = client.get("/clusters?a=1&b=x y", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?next=/clusters%3Fa%3D1%26b%3Dx%2520y"
    hx = client.get("/clusters", headers={"HX-Request": "TRUE"}, follow_redirects=False)
    assert (hx.status_code, hx.headers["HX-Redirect"], hx.content) == (204, "/login?next=/clusters", b"")


def test_web_gate_ignores_bearer_header(real):
    client, app = real
    client.cookies.clear()
    resp = client.get("/clusters", headers={"Authorization": f"Bearer {_token(app)}"}, follow_redirects=False)
    assert resp.status_code == 303


# ── bootstrap_admin ───────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _users(engine) -> list[tuple]:
    with Session(engine) as session:
        return [(u.username, u.created_at, u.is_active) for u in session.query(User).order_by(User.id)]


def test_bootstrap_creates_admin_once(engine, caplog):
    settings = _settings(auth_admin_username="root", auth_admin_password="pw-1")
    with caplog.at_level(logging.INFO, logger="greenhouse_server.auth"):
        bootstrap_admin(engine, settings)
        bootstrap_admin(engine, _settings(auth_admin_username="other", auth_admin_password="pw-2"))
    assert _users(engine) == [("root", FROZEN_TS, True)]
    assert caplog.messages == ["Bootstrapped initial admin user 'root' from environment."]


@pytest.mark.parametrize(
    ("username", "password"),
    [(None, "pw"), ("root", None), ("", "pw"), ("root", "")],
    ids=["no-user", "no-pw", "empty-user", "empty-pw"],
)
def test_bootstrap_without_credentials_warns(engine, caplog, username, password):
    with caplog.at_level(logging.WARNING, logger="greenhouse_server.auth"):
        bootstrap_admin(engine, _settings(auth_admin_username=username, auth_admin_password=password))
    assert _users(engine) == []
    assert caplog.messages == [
        "Auth is enabled but no users exist and GREENHOUSE_AUTH_ADMIN_USERNAME / GREENHOUSE_AUTH_ADMIN_PASSWORD "
        "are not set. The API will reject every request with 401 until a user is created."
    ]


def test_bootstrap_is_skipped_when_auth_disabled(engine):
    bootstrap_admin(engine, _settings(auth_enabled=False, auth_admin_username="root", auth_admin_password="pw"))
    assert _users(engine) == []


# ── MCP bearer gate ───────────────────────────────────────────────────────────


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
        ("", {"Authorization": "Bearer "}, 401, "Invalid MCP token"),  # empty token: closed, but 401 not 503
        ("", {"Authorization": "Bearer x"}, 401, "Invalid MCP token"),
    ],
)
def test_mcp_gate(clean_env, mcp_token, headers, status, detail):
    client, _, engine = _client(mcp_token=mcp_token)
    try:
        resp = client.get("/mcp", headers=headers)
        if status is None:
            assert resp.status_code not in (401, 503)
        else:
            assert (resp.status_code, resp.json()) == (status, {"detail": detail})
    finally:
        engine.dispose()
