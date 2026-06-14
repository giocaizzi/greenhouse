"""Server test fixtures — full-stack TestClient with in-memory SQLite.

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
``rainpoint.ik10pw`` / ``tuya.tr301z`` model keys plus the legacy aliases
(``tuya_cloud`` etc.) — its factories build :class:`FakeIrrigatorAdapter` /
:class:`FakeSensorAdapter` instances from ``tests/fake_devices.py``. The
registry is stored on ``app.state.fake_devices`` so tests can fetch the
per-irrigator fake and assert on its recorded ``calls``.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from fake_devices import FakeIrrigatorAdapter, FakeSensorAdapter
from greenhouse_core.devices import DeviceRegistry
from greenhouse_server.app import create_app
from greenhouse_server.auth import AuthenticatedUser, require_user, require_web_user
from greenhouse_server.config import Settings
from greenhouse_server.deps import get_device_registry, get_tuya_cloud

TEST_ADMIN_USERNAME = "test-admin"
TEST_ADMIN_PASSWORD = "test-admin-pw-123"
TEST_AUTH_SECRET = "unit-test-jwt-secret-do-not-use-in-prod-please"


class FakeDeviceWiring:
    """Holds the shared fakes alongside the registry that resolves them.

    Tests grab ``wiring.irrigator`` to assert on ``start_calls`` / ``stop_calls``
    and ``wiring.sensor`` to inspect or drive sensor reads.
    """

    def __init__(self) -> None:
        self.irrigator = FakeIrrigatorAdapter()
        self.sensor = FakeSensorAdapter()
        self.registry = DeviceRegistry()
        # Register the production model keys + all legacy aliases. The fixtures
        # exercise routes whose seed data uses ``type="tuya_cloud"`` and
        # ``type="soil_moisture"``; we want one fake shared across keys so a
        # test can run the engine and inspect the same adapter.
        for key in ("rainpoint.ik10pw", "tuya_cloud", "tuya_local", "fake.irrigator", ""):
            self.registry.register_irrigator(key, lambda adapter=self.irrigator: adapter)
        for key in (
            "tuya.tr301z",
            "soil_moisture",
            "temp_humidity",
            "light",
            "fake.sensor",
            "",
        ):
            self.registry.register_sensor(key, lambda adapter=self.sensor: adapter)


def _make_stubbed_app(*, bypass_auth: bool, **settings_override):
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
    base = {
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
    application.dependency_overrides[get_tuya_cloud] = lambda: None

    if bypass_auth:
        synthetic = AuthenticatedUser(id=1, username=TEST_ADMIN_USERNAME)
        application.dependency_overrides[require_user] = lambda: synthetic
        application.dependency_overrides[require_web_user] = lambda: synthetic

    return application, engine


@pytest.fixture
def app():
    """Default app — auth bypassed for ergonomic test setup. See module docstring."""
    application, engine = _make_stubbed_app(bypass_auth=True)
    yield application
    engine.dispose()


@pytest.fixture
def app_real_auth():
    """App with real auth enforcement. Used by tests that verify 401/redirect/login."""
    application, engine = _make_stubbed_app(bypass_auth=False)
    yield application
    engine.dispose()


@pytest.fixture
def client(app):
    """TestClient on the auth-bypassed app — drop-in for all existing tests."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def anonymous_client(app_real_auth):
    """TestClient on the real-auth app with no credentials — exercises 401 paths."""
    return TestClient(app_real_auth, raise_server_exceptions=False)


@pytest.fixture
def authed_real_client(app_real_auth):
    """TestClient on the real-auth app that has performed a real login."""
    tc = TestClient(app_real_auth, raise_server_exceptions=False)
    resp = tc.post(
        "/api/v1/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, f"Real-auth login failed: {resp.status_code} {resp.text}"
    tc.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return tc


@pytest.fixture
def auth_disabled_app():
    """App with auth_enabled=False — exercises the dev escape-hatch path."""
    application, engine = _make_stubbed_app(bypass_auth=False, auth_enabled=False)
    yield application
    engine.dispose()


@pytest.fixture
def seeded_client(client):
    """Client with a pre-populated cluster, plant, sensor, irrigator, and config."""
    # Create cluster
    resp = client.post("/api/v1/clusters", json={"name": "Test Cluster", "environment": "indoor"})
    assert resp.status_code == 201

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
    assert resp.status_code == 201

    # Add sensor
    resp = client.post(
        "/api/v1/clusters/1/sensors",
        json={
            "tuya_device_id": "fake_sensor_001",
            "name": "Test Sensor",
            "type": "soil_moisture",
            "plant_id": 1,
        },
    )
    assert resp.status_code == 201

    # Add irrigator
    resp = client.post(
        "/api/v1/clusters/1/irrigator",
        json={
            "tuya_device_id": "fake_irrigator_001",
            "name": "Test Irrigator",
            "type": "tuya_cloud",
        },
    )
    assert resp.status_code == 201

    # Set config
    resp = client.put(
        "/api/v1/clusters/1/config",
        json={"mode": "smart", "duration_minutes": 2, "interval_hours": 12, "auto_run": True},
    )
    assert resp.status_code == 200

    return client
