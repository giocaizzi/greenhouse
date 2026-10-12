"""Server test fixtures — full-stack TestClient with in-memory SQLite.

Registered as a global plugin by ``tests/conftest.py`` (``pytest_plugins``), not
as a directory conftest: pytest binds a conftest's fixtures to the first
``Package`` node collected for its directory, so a command line that mixed
directories (``tests/server/X tests/test_core.py tests/server/Y``) collected a
second ``server`` package that could not see them ("fixture 'client' not
found"). Plugin fixtures are visible everywhere, so any path combination works.

Two app fixtures exist:

- `app` (default): auth dependencies are dependency-overridden to a synthetic
  admin user so every TestClient request — even raw inline ones — passes auth
  without per-test boilerplate. The auth machinery itself is still wired (the
  /auth/login endpoint works against a bootstrapped admin), but the
  per-request gate short-circuits. This is what every non-auth test should use.
- `app_real_auth`: same stubs, but no auth override. Used by `test_auth.py` to
  verify the real 401/redirect/login flow end to end.

`client` fixture builds a TestClient on `app` (auth bypassed via override).
`anonymous_client` uses `app_real_auth` and sends no credentials — so it
actually triggers 401 / login-redirect.

Device wiring: a real :class:`DeviceRegistry` is registered for the canonical
``rainpoint.ik10pw`` / ``tuya.tr301z`` model keys (plus ``fake.*`` test keys) —
its factories build :class:`FakeIrrigatorAdapter` /
:class:`FakeSensorAdapter` instances from ``tests/fake_devices.py``. The
registry is stored on ``app.state.fake_devices`` so tests can fetch the
per-irrigator fake and assert on its recorded ``calls``.
"""

import copy
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from http import HTTPStatus
from typing import Any

import fastapi_mcp.server
import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.routing import BaseRoute

from fake_devices import FakeIrrigatorAdapter, FakeSensorAdapter
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.models import Base
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.app import create_app
from greenhouse_server.auth import AuthenticatedUser, require_user, require_web_user
from greenhouse_server.config import Settings
from greenhouse_server.deps import get_device_gateway, get_device_registry, get_repository
from greenhouse_server.scheduler import scheduler as bg_scheduler
from greenhouse_server.scheduler import start_scheduler, stop_scheduler

TEST_ADMIN_USERNAME = "test-admin"
# The server tests' fake credentials, defined once here (S105 can only be satisfied by not defining them).
TEST_ADMIN_PASSWORD = "test-admin-pw-123"  # noqa: S105 — fake test credential
TEST_AUTH_SECRET = "unit-test-jwt-secret-do-not-use-in-prod-please"  # noqa: S105 — fake test credential
TEST_MCP_TOKEN = "contract-mcp-token-0123456789abcdef"  # noqa: S105 — fake test credential
TEST_NTFY_TOKEN = "test-ntfy-token"  # noqa: S105 — fake test credential


@contextmanager
def override_repository(app: FastAPI) -> Iterator[IrrigationRepository]:
    """Serve ``app``'s repository dependency from a fresh in-memory database; undo it on exit."""
    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    repo = IrrigationRepository(session)
    app.dependency_overrides[get_repository] = lambda: repo
    try:
        yield repo
    finally:
        app.dependency_overrides.pop(get_repository, None)
        session.close()
        engine.dispose()


def app_of(client: TestClient) -> FastAPI:
    """The FastAPI application ``client`` drives (``TestClient.app`` is typed as a bare ASGI app)."""
    application = client.app
    assert isinstance(application, FastAPI)
    return application


def run_core_job(job_id: str) -> None:
    """Run a built-in scheduler job through the callable the scheduler registered for ``job_id``.

    This is the path APScheduler takes when the job fires, so a test drives the job body
    without reaching into the scheduler module's private functions.
    """
    job = bg_scheduler.get_job(job_id)
    assert job is not None, f"built-in job {job_id!r} is not registered"
    job.func()


class FakeDeviceWiring:
    """Holds the shared fakes alongside the registry that resolves them.

    Tests grab ``wiring.irrigator`` to assert on ``start_calls`` / ``stop_calls``
    and ``wiring.sensor`` to inspect or drive sensor reads.
    """

    def __init__(self) -> None:
        """Build the shared fakes and a registry that resolves every seeded model key to them."""
        self.irrigator = FakeIrrigatorAdapter()
        self.sensor = FakeSensorAdapter()
        self.registry = DeviceRegistry()
        # Register the production model keys the seed data uses (plus the
        # ``fake.*`` test keys); one fake is shared across keys so a test can
        # run the engine and inspect the same adapter.
        irrigator, sensor = self.irrigator, self.sensor
        for key in ("rainpoint.ik10pw", "fake.irrigator"):
            self.registry.register_irrigator(key, lambda: irrigator)
        for key in ("tuya.tr301z", "fake.sensor"):
            self.registry.register_sensor(key, lambda: sensor)


def _schema_signature(routes: Sequence[BaseRoute]) -> tuple[tuple[object, ...], ...]:
    """What ``routes`` contribute to the OpenAPI schema: each API route's path, methods, endpoint and dependencies.

    Other routes (docs pages, static mounts, the MCP endpoint) never reach the schema, and some are
    per-app closures, so they are left out of the key.
    """
    return tuple(
        (
            context.path,
            frozenset(context.methods or ()),
            context.endpoint,
            tuple(depends.dependency for depends in context.dependencies),
        )
        for context in iter_route_contexts(routes)
        if isinstance(context.original_route, APIRoute) and context.include_in_schema
    )


@pytest.fixture(autouse=True, scope="session")
def _cached_mcp_openapi() -> Iterator[None]:
    """Generate the MCP mount's OpenAPI schema once per route table, not once per app build.

    ``FastApiMCP.setup_server`` calls ``get_openapi`` on every ``create_app``, and that is most
    of an app build. Every build includes the same module-level endpoints, so the schema is
    cached on the route signature; each caller gets a deep copy. The MCP and OpenAPI goldens
    catch a cache that serves the wrong schema.
    """
    cache: dict[tuple[object, ...], dict[str, Any]] = {}

    def cached_get_openapi(*, routes: Sequence[BaseRoute], **header: Any) -> dict[str, Any]:  # noqa: ANN401 — mirrors get_openapi's keyword header
        key = (tuple(sorted(header.items())), _schema_signature(routes))
        if key not in cache:
            cache[key] = get_openapi(routes=routes, **header)
        return copy.deepcopy(cache[key])

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fastapi_mcp.server, "get_openapi", cached_get_openapi)
        yield


def _make_stubbed_app(*, bypass_auth: bool, **settings_override: object) -> tuple[FastAPI, Engine]:
    """Construct a create_app instance with the standard device/cloud stubs.

    When `bypass_auth=True` the require_user / require_web_user dependencies
    are overridden to a synthetic admin so any TestClient request passes auth
    without going through real login. The auth routes themselves still work
    (the bootstrap admin exists), so /auth/login + /auth/me round-trips are
    real in either mode.
    """
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    base: dict[str, object] = {
        "db_url": "sqlite://",
        "enable_scheduler": False,
        "auth_enabled": True,
        "auth_secret_key": TEST_AUTH_SECRET,
        "auth_admin_username": TEST_ADMIN_USERNAME,
        "auth_admin_password": TEST_ADMIN_PASSWORD,
    }
    base.update(settings_override)
    application = create_app(Settings(**base), engine=engine)

    wiring = FakeDeviceWiring()
    application.state.fake_devices = wiring
    application.dependency_overrides[get_device_registry] = lambda: wiring.registry
    application.dependency_overrides[get_device_gateway] = lambda: None

    if bypass_auth:
        synthetic = AuthenticatedUser(id=1, username=TEST_ADMIN_USERNAME)
        application.dependency_overrides[require_user] = lambda: synthetic
        application.dependency_overrides[require_web_user] = lambda: synthetic

    return application, engine


@pytest.fixture
def app() -> Iterator[FastAPI]:
    """Default app — auth bypassed for ergonomic test setup. See module docstring."""
    application, engine = _make_stubbed_app(bypass_auth=True)
    yield application
    engine.dispose()


@pytest.fixture
def app_real_auth() -> Iterator[FastAPI]:
    """App with real auth enforcement. Used by tests that verify 401/redirect/login."""
    application, engine = _make_stubbed_app(bypass_auth=False)
    yield application
    engine.dispose()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """TestClient on the auth-bypassed app — drop-in for all existing tests."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def anonymous_client(app_real_auth: FastAPI) -> TestClient:
    """TestClient on the real-auth app with no credentials — exercises 401 paths."""
    return TestClient(app_real_auth, raise_server_exceptions=False)


@pytest.fixture
def authed_real_client(app_real_auth: FastAPI) -> TestClient:
    """TestClient on the real-auth app that has performed a real login."""
    tc = TestClient(app_real_auth, raise_server_exceptions=False)
    resp = tc.post(
        "/api/v1/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == HTTPStatus.OK, f"Real-auth login failed: {resp.status_code} {resp.text}"
    tc.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return tc


@pytest.fixture
def auth_disabled_app() -> Iterator[FastAPI]:
    """App with auth_enabled=False — exercises the dev escape-hatch path."""
    application, engine = _make_stubbed_app(bypass_auth=False, auth_enabled=False)
    yield application
    engine.dispose()


@pytest.fixture
def running_scheduler(app: FastAPI) -> Iterator[BackgroundScheduler]:
    """Start the process-wide APScheduler for one test, without firing any job.

    Apps are built with ``enable_scheduler=False`` so the scheduler normally
    stays stopped and every job is merely *pending*. Tests that need the real
    running-scheduler semantics (job stores, computed ``next_run_time``, the
    running-only web controls) request this fixture: it starts the scheduler in
    APScheduler's ``paused`` state — ``scheduler.running`` is True but the main
    loop processes nothing — and shuts it down afterwards. Depends on ``app``
    so the app's ``init_scheduler`` (which needs a stopped scheduler) runs first.
    """
    del app  # requested only for its side effect

    start_scheduler(paused=True)
    try:
        yield bg_scheduler
    finally:
        stop_scheduler()


@pytest.fixture
def seeded_client(client: TestClient) -> TestClient:
    """Client with a pre-populated cluster, plant, sensor, irrigator, and config."""
    # Create cluster
    resp = client.post("/api/v1/clusters", json={"name": "Test Cluster", "environment": "indoor"})
    assert resp.status_code == HTTPStatus.CREATED

    # Add plant
    resp = client.post(
        "/api/v1/clusters/1/plants",
        json={
            "species": "Monstera deliciosa",
            "category": "tropical",
            "water_needs": "medium",
            "ideal_temp_min": 18.0,
            "ideal_temp_max": 27.0,
            "ideal_humidity_min": 60.0,
            "ideal_humidity_max": 80.0,
        },
    )
    assert resp.status_code == HTTPStatus.CREATED

    # Add sensor
    resp = client.post(
        "/api/v1/clusters/1/sensors",
        json={
            "tuya_device_id": "fake_sensor_001",
            "name": "Test Sensor",
            "type": "tuya.tr301z",
            "plant_id": 1,
        },
    )
    assert resp.status_code == HTTPStatus.CREATED

    # Add irrigator
    resp = client.post(
        "/api/v1/clusters/1/irrigator",
        json={
            "tuya_device_id": "fake_irrigator_001",
            "name": "Test Irrigator",
            "type": "rainpoint.ik10pw",
        },
    )
    assert resp.status_code == HTTPStatus.CREATED

    # Set config
    resp = client.put(
        "/api/v1/clusters/1/config",
        json={"mode": "smart", "duration_minutes": 2, "interval_hours": 12, "auto_run": True},
    )
    assert resp.status_code == HTTPStatus.OK

    return client
