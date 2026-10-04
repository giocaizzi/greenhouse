"""Determinism kit and golden-file assertions for the characterization suite.

Golden tests pin *what the code does today* so a behavior-preserving refactor can
prove it changed nothing. They must be hermetic and reproducible:

- the clock is frozen at :data:`FROZEN_INSTANT` (spring, mid-morning UTC — outside
  the seeded 00:00–05:00 quiet hours and far from day/season boundaries);
- weather is served by :class:`OfflineWeather` (the same ``None`` the real client
  returns when Open-Meteo is unreachable), so no test touches the network;
- environment variables that ``Settings`` / the CLI read are cleared.

Golden files live in ``tests/golden/``. A missing or different golden fails the
test. ``GOLDEN_UPDATE=1`` (re)writes goldens; it is meant only for the commit that
creates them — never to make a refactor pass (AGENTS.md "Golden policy").
"""

from __future__ import annotations

import difflib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).parent / "golden"
UPDATE_ENV_VAR = "GOLDEN_UPDATE"

FROZEN_INSTANT = datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC)
FROZEN_TS = int(FROZEN_INSTANT.timestamp())

# Prefixes of every environment variable the server settings, core and CLI read.
ENV_PREFIXES = ("IRRIGATION_", "GREENHOUSE_", "TUYA_")

_MAX_DIFF_LINES = 120


class OfflineWeather:
    """Stand-in for ``WeatherClient`` that behaves like Open-Meteo being unreachable."""

    def get_current(self) -> None:
        return None

    def get_forecast(self, hours: int = 6) -> None:  # noqa: ARG002 — signature mirrors WeatherClient
        return None


def install_offline_weather(app: Any) -> OfflineWeather:
    """Replace the app's weather client (state + dependency) with :class:`OfflineWeather`."""
    from greenhouse_server.deps import get_weather_client

    stub = OfflineWeather()
    app.state.weather_client = stub
    app.dependency_overrides[get_weather_client] = lambda: stub
    return stub


def to_canonical_json(value: Any) -> str:
    """Serialize ``value`` deterministically (sorted keys, stable indentation, trailing newline)."""
    return json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False, default=str) + "\n"


def assert_golden(name: str, actual: str) -> None:
    """Assert ``actual`` equals the golden file ``tests/golden/<name>``.

    With ``GOLDEN_UPDATE=1`` the golden is written instead and the test passes.
    """
    path = GOLDEN_DIR / name
    if os.environ.get(UPDATE_ENV_VAR) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    if not path.exists():
        raise AssertionError(f"golden file missing: {path} (create it with {UPDATE_ENV_VAR}=1)")
    expected = path.read_text(encoding="utf-8")
    if actual == expected:
        return
    diff = list(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile=f"golden/{name}",
            tofile="actual",
            lineterm="",
        )
    )
    shown = "\n".join(diff[:_MAX_DIFF_LINES])
    more = f"\n… {len(diff) - _MAX_DIFF_LINES} more diff lines" if len(diff) > _MAX_DIFF_LINES else ""
    raise AssertionError(f"output differs from golden/{name}:\n{shown}{more}")


def assert_golden_json(name: str, value: Any) -> None:
    """Assert the canonical JSON form of ``value`` equals ``tests/golden/<name>``."""
    assert_golden(name, to_canonical_json(value))
