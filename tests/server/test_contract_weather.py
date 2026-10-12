"""Characterization: ``WeatherClient`` (Open-Meteo) — URLs, parsing, cache TTL, failure → ``None`` (gap W1).

No network: ``urllib.request.urlopen`` is replaced with a recorder serving canned
JSON, and the module's ``time`` is swapped for a fake whose ``monotonic`` we drive,
so the 600-second cache TTL is exercised exactly at its boundary.
"""

from __future__ import annotations

import json
import urllib.request
from types import SimpleNamespace
from typing import Self

import pytest

from golden import install_offline_weather
from greenhouse_server.services import weather as weather_module
from greenhouse_server.services.weather import WeatherClient

from .conftest import _make_stubbed_app

BASE = "https://api.open-meteo.com/v1/forecast"
CURRENT_URL = (
    f"{BASE}?latitude=45.464&longitude=9.189"
    "&current=temperature_2m,apparent_temperature,precipitation,relative_humidity_2m&timezone=UTC"
)
FORECAST_URL = (
    f"{BASE}?latitude=45.464&longitude=9.189"
    "&hourly=precipitation,temperature_2m,relative_humidity_2m&forecast_days=2&timezone=UTC"
)


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeNet:
    """Recorder for ``urlopen(url, timeout=…)``; each call pops the next scripted response."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch ``urlopen`` and the module clock so every request is recorded and scripted."""
        self.calls: list[tuple[str, object]] = []
        self.responses: list[object] = []
        self.now = 1000.0
        monkeypatch.setattr(urllib.request, "urlopen", self._urlopen)  # the module weather_module.urllib names
        monkeypatch.setattr(weather_module, "time", SimpleNamespace(monotonic=lambda: self.now))

    def queue(self, *responses: object) -> None:
        """Script the next responses (payload dicts, raw bytes or exceptions to raise)."""
        self.responses.extend(responses)

    def _urlopen(self, url: str, timeout: float | None = None) -> _Resp:
        self.calls.append((url, timeout))
        resp = self.responses.pop(0)
        if isinstance(resp, BaseException):
            raise resp
        body = resp if isinstance(resp, bytes) else json.dumps(resp).encode()
        return _Resp(body)


def _temperature(client: WeatherClient) -> float | None:
    """The current temperature (the client must have answered)."""
    current = client.get_current()
    assert current is not None
    return current["temperature"]


def _precipitation(client: WeatherClient) -> float | None:
    """The default window's precipitation (the client must have answered)."""
    forecast = client.get_forecast()
    assert forecast is not None
    return forecast["precipitation_mm"]


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> FakeNet:
    """A scripted network for the weather client."""
    return FakeNet(monkeypatch)


CURRENT_PAYLOAD = {
    "current": {
        "temperature_2m": 18.4,
        "apparent_temperature": 17.1,
        "precipitation": 0.2,
        "relative_humidity_2m": 71,
        "wind_speed_10m": 9.0,
    }
}
HOURLY_PAYLOAD = {
    "hourly": {
        "precipitation": [0.0, 0.5, 1.25, 0.0, 0.0, 0.25, 3.0, 3.0],
        "temperature_2m": [14.0, 15.5, 17.0, 19.0, 18.0, 16.0, 30.0, 30.0],
        "relative_humidity_2m": [80, 70, 60, 50, 55, 65, 10, 10],
    }
}
CACHE_TTL_SECONDS = 600  # pinned: ``weather._FORECAST_CACHE_TTL``
CURRENT_TEMPERATURE = CURRENT_PAYLOAD["current"]["temperature_2m"]
SIX_HOUR_PRECIPITATION_MM = 2.0  # sum of HOURLY_PAYLOAD's first six hours
REFETCHED_TEMPERATURE = 25.0
REFETCHED_PRECIPITATION_MM = 9.0


def test_constructor_defaults(net: FakeNet) -> None:
    """Defaults show in the request: Milan's coordinates, UTC, an 8 s timeout, and a 600 s cache."""
    net.queue(CURRENT_PAYLOAD, CURRENT_PAYLOAD)
    client = WeatherClient()
    client.get_current()
    assert net.calls == [(CURRENT_URL, 8)]  # latitude=45.464, longitude=9.189, timezone=UTC
    net.now += CACHE_TTL_SECONDS - 0.001
    client.get_current()
    assert len(net.calls) == 1  # still cached just inside the TTL
    net.now += 0.001
    client.get_current()
    assert net.calls == [(CURRENT_URL, 8), (CURRENT_URL, 8)]  # refetched at the TTL


class TestCurrent:
    """Tests for current."""

    def test_exact_url_timeout_and_mapping(self, net: FakeNet) -> None:
        """Exact URL timeout and mapping."""
        net.queue(CURRENT_PAYLOAD)
        assert WeatherClient().get_current() == {
            "temperature": 18.4,
            "feels_like": 17.1,
            "precipitation": 0.2,
            "humidity": 71,
        }
        assert net.calls == [(CURRENT_URL, 8)]

    def test_custom_location_timezone_and_timeout_are_interpolated_verbatim(self, net: FakeNet) -> None:
        """Custom location timezone and timeout are interpolated verbatim."""
        net.queue(CURRENT_PAYLOAD)
        WeatherClient(lat=-33.5, lon=151.25, timeout=3, tz="Australia/Sydney").get_current()
        assert net.calls == [
            (
                (
                    f"{BASE}?latitude=-33.5&longitude=151.25"
                    "&current=temperature_2m,apparent_temperature,precipitation,relative_humidity_2m"
                    "&timezone=Australia/Sydney"
                ),
                3,
            )
        ]

    def test_missing_current_block_maps_to_all_none_and_is_cached(self, net: FakeNet) -> None:
        """Missing current block maps to all none and is cached."""
        net.queue({"hourly": {}})
        client = WeatherClient()
        expected = {"temperature": None, "feels_like": None, "precipitation": None, "humidity": None}
        assert client.get_current() == expected
        assert client.get_current() == expected
        assert len(net.calls) == 1

    @pytest.mark.parametrize("failure", [OSError("dns"), TimeoutError("slow"), b"not json", b"[1, 2]"])
    def test_failure_returns_none_and_is_not_cached(self, net: FakeNet, failure: Exception | bytes) -> None:
        """Failure returns none and is not cached."""
        net.queue(failure, CURRENT_PAYLOAD)
        client = WeatherClient()
        assert client.get_current() is None
        assert _temperature(client) == CURRENT_TEMPERATURE
        assert [url for url, _ in net.calls] == [CURRENT_URL, CURRENT_URL]

    def test_cache_ttl_boundary(self, net: FakeNet) -> None:
        """Cache TTL boundary."""
        net.queue(CURRENT_PAYLOAD, {"current": {"temperature_2m": REFETCHED_TEMPERATURE}})
        client = WeatherClient()
        assert _temperature(client) == CURRENT_TEMPERATURE  # fetched at t=1000
        net.now = 1000 + CACHE_TTL_SECONDS - 0.001
        assert _temperature(client) == CURRENT_TEMPERATURE  # still cached
        net.now = 1000 + CACHE_TTL_SECONDS
        assert _temperature(client) == REFETCHED_TEMPERATURE  # TTL is strict: age 600 refetches
        assert [url for url, _ in net.calls] == [CURRENT_URL, CURRENT_URL]


class TestForecast:
    """Tests for forecast."""

    def test_exact_url_and_six_hour_aggregate(self, net: FakeNet) -> None:
        """Exact URL and six hour aggregate."""
        net.queue(HOURLY_PAYLOAD)
        assert WeatherClient().get_forecast() == {
            "precipitation_mm": 2.0,
            "max_temp": 19.0,
            "min_temp": 14.0,
            "avg_humidity": 63.333333333333336,
        }
        assert net.calls == [(FORECAST_URL, 8)]

    def test_window_clamps_to_available_hours(self, net: FakeNet) -> None:
        """Window clamps to available hours."""
        net.queue(HOURLY_PAYLOAD)
        assert WeatherClient().get_forecast(hours=48) == {
            "precipitation_mm": 8.0,
            "max_temp": 30.0,
            "min_temp": 14.0,
            "avg_humidity": 50.0,
        }

    def test_window_is_sized_by_precipitation_length_only(self, net: FakeNet) -> None:
        """Window is sized by precipitation length only."""
        net.queue({"hourly": {"precipitation": [1.0, 2.0, 3.0], "temperature_2m": [20.0], "relative_humidity_2m": []}})
        assert WeatherClient().get_forecast(hours=3) == {
            "precipitation_mm": 6.0,
            "max_temp": 20.0,
            "min_temp": 20.0,
            "avg_humidity": None,
        }

    @pytest.mark.parametrize(
        ("payload", "hours"),
        [({"hourly": {"precipitation": []}}, 6), ({}, 6)],
    )
    def test_empty_window_returns_none_and_is_not_cached(
        self, net: FakeNet, payload: dict[str, object], hours: int
    ) -> None:
        """Empty window returns none and is not cached."""
        net.queue(payload, HOURLY_PAYLOAD)
        client = WeatherClient()
        assert client.get_forecast(hours=hours) is None
        assert _precipitation(client) == SIX_HOUR_PRECIPITATION_MM
        assert [url for url, _ in net.calls] == [FORECAST_URL, FORECAST_URL]

    @pytest.mark.parametrize("hours", [0, -1, -48])
    def test_non_positive_hours_returns_none_without_a_request(self, net: FakeNet, hours: int) -> None:
        """A window under one hour has no forecast; a negative one no longer slices from the end."""
        net.queue(HOURLY_PAYLOAD)
        client = WeatherClient()
        assert client.get_forecast(hours=hours) is None
        assert net.calls == []
        assert _precipitation(client) == SIX_HOUR_PRECIPITATION_MM  # refused call cached nothing

    @pytest.mark.parametrize("failure", [OSError("reset"), b"{", b'{"hourly": {"precipitation": ["x"]}}'])
    def test_failure_returns_none(self, net: FakeNet, failure: Exception | bytes) -> None:
        """Failure returns none."""
        net.queue(failure)
        assert WeatherClient().get_forecast() is None

    def test_cache_ttl_boundary_and_independent_caches(self, net: FakeNet) -> None:
        """Cache TTL boundary and independent caches."""
        net.queue(HOURLY_PAYLOAD, CURRENT_PAYLOAD, {"hourly": {"precipitation": [REFETCHED_PRECIPITATION_MM]}})
        client = WeatherClient()
        client.get_forecast()
        client.get_current()  # separate cache: forecast cache does not serve current
        net.now = 1599.0
        assert _precipitation(client) == SIX_HOUR_PRECIPITATION_MM
        net.now = 1600.0
        assert _precipitation(client) == REFETCHED_PRECIPITATION_MM
        assert [url for url, _ in net.calls] == [FORECAST_URL, CURRENT_URL, FORECAST_URL]

    def test_forecast_cache_is_keyed_by_hours(self, net: FakeNet) -> None:
        """Within the TTL each ``hours`` window gets its own aggregate (and cache entry), not the first one's."""
        net.queue(HOURLY_PAYLOAD, HOURLY_PAYLOAD)
        client = WeatherClient()
        six = client.get_forecast(hours=6)
        two = client.get_forecast(hours=2)
        assert two == {"precipitation_mm": 0.5, "max_temp": 15.5, "min_temp": 14.0, "avg_humidity": 75.0}
        assert two != six
        assert client.get_forecast(hours=6) == six  # both windows are now served from the cache
        assert client.get_forecast(hours=2) == two
        assert [url for url, _ in net.calls] == [FORECAST_URL, FORECAST_URL]


@pytest.mark.usefixtures("clean_env")
def test_app_wires_settings_location_and_preference_timezone(net: FakeNet) -> None:
    """``create_app`` builds the client from ``weather_lat``/``weather_lon`` and the startup timezone."""
    app, engine = _make_stubbed_app(bypass_auth=True, weather_lat=1.5, weather_lon=-2.25)
    try:
        client = app.state.weather_client
        assert isinstance(client, WeatherClient)
        net.queue(CURRENT_PAYLOAD)
        client.get_current()
        assert net.calls == [
            (
                (
                    f"{BASE}?latitude=1.5&longitude=-2.25"
                    "&current=temperature_2m,apparent_temperature,precipitation,relative_humidity_2m&timezone=UTC"
                ),
                8,
            )
        ]
        install_offline_weather(app)
    finally:
        engine.dispose()
