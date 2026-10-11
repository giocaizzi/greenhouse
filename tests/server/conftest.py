"""Re-exports for ``from server.conftest import …`` in server tests.

The fixtures themselves live in :mod:`server.fixtures`, registered globally by
``tests/conftest.py`` (see that module's docstring for why). Only helpers and
constants are re-exported here — never fixture functions, which a conftest would
register a second time.
"""

from server.fixtures import (
    TEST_ADMIN_PASSWORD,
    TEST_ADMIN_USERNAME,
    TEST_AUTH_SECRET,
    TEST_MCP_TOKEN,
    TEST_NTFY_TOKEN,
    FakeDeviceWiring,
    _make_stubbed_app,
    run_core_job,
)

__all__ = [
    "TEST_ADMIN_PASSWORD",
    "TEST_ADMIN_USERNAME",
    "TEST_AUTH_SECRET",
    "TEST_MCP_TOKEN",
    "TEST_NTFY_TOKEN",
    "FakeDeviceWiring",
    "_make_stubbed_app",
    "run_core_job",
]
