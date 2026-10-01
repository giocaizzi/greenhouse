"""Intent tests for the determinism kit in ``tests/golden.py``."""

import os
import time
from datetime import UTC, datetime

import pytest

from golden import FROZEN_INSTANT, FROZEN_TS, OfflineWeather, assert_golden, to_canonical_json


def test_frozen_clock_pins_time_and_datetime(frozen_clock):
    assert int(time.time()) == FROZEN_TS
    assert datetime.now(UTC) == FROZEN_INSTANT


def test_clean_env_fixture(monkeypatch, request):
    monkeypatch.setenv("IRRIGATION_DB_URL", "sqlite:///should-not-leak.db")
    monkeypatch.setenv("GREENHOUSE_MCP_TOKEN", "leak")
    clean_env = request.getfixturevalue("clean_env")
    assert not [n for n in os.environ if n.startswith(("IRRIGATION_", "GREENHOUSE_", "TUYA_"))]
    assert os.environ["TZ"] == "UTC"
    assert os.getcwd() == str(clean_env)


def test_offline_weather_mirrors_unreachable_client():
    weather = OfflineWeather()
    assert weather.get_current() is None
    assert weather.get_forecast(hours=12) is None


def test_canonical_json_is_order_independent():
    assert to_canonical_json({"b": 1, "a": [2, 1]}) == to_canonical_json({"a": [2, 1], "b": 1})
    assert to_canonical_json({"a": 1}).endswith("\n")


def test_assert_golden_reports_diff(tmp_path, monkeypatch):
    import golden

    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path)
    monkeypatch.delenv("GOLDEN_UPDATE", raising=False)
    (tmp_path / "sample.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    assert_golden("sample.txt", "alpha\nbeta\n")
    with pytest.raises(AssertionError, match="differs from golden/sample.txt"):
        assert_golden("sample.txt", "alpha\ngamma\n")
    with pytest.raises(AssertionError, match="golden file missing"):
        assert_golden("absent.txt", "x")
