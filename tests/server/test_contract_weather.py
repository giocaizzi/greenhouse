"""Characterization: ``WeatherClient`` (Open-Meteo) — URLs, parsing, cache TTL, failure → ``None`` (gap W1).

No network: ``urllib.request.urlopen`` is replaced with a recorder serving canned
JSON, and the module's ``time`` is swapped for a fake whose ``monotonic`` we drive,
so the 600-second cache TTL is exercised exactly at its boundary.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from greenhouse_server.services import weather as weather_module
from greenhouse_server.services.weather import WeatherClient

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

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class FakeNet:
    """Recorder for ``urlopen(url, timeout=…)``; each call pops the next scripted response."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[tuple[str, object]] = []
        self.responses: list[object] = []
        self.now = 1000.0
        monkeypatch.setattr(weather_module.urllib.request, "urlopen", self._urlopen)
        monkeypatch.setattr(weather_module, "time", SimpleNamespace(monotonic=lambda: self.now))

    def queue(self, *responses: object) -> None:
        self.responses.extend(responses)

    def _urlopen(self, url, timeout=None):
        self.calls.append((url, timeout))
        resp = self.responses.pop(0)
        if isinstance(resp, BaseException):
            raise resp
        body = resp if isinstance(resp, bytes) else json.dumps(resp).encode()
        return _Resp(body)


@pytest.fixture
def net(monkeypatch) -> FakeNet:
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


def test_constructor_defaults():
    client = WeatherClient()
    assert (client._lat, client._lon, client._timeout, client._tz) == (45.464, 9.189, 8, "UTC")
    assert weather_module._FORECAST_CACHE_TTL == 600


class TestCurrent:
    def test_exact_url_timeout_and_mapping(self, net):
        net.queue(CURRENT_PAYLOAD)
        assert WeatherClient().get_current() == {
            "temperature": 18.4,
            "feels_like": 17.1,
            "precipitation": 0.2,
            "humidity": 71,
        }
        assert net.calls == [(CURRENT_URL, 8)]

    def test_custom_location_timezone_and_timeout_are_interpolated_verbatim(self, net):
        net.queue(CURRENT_PAYLOAD)
        WeatherClient(lat=-33.5, lon=151.25, timeout=3, tz="Australia/Sydney").get_current()
        assert net.calls == [
            (
                f"{BASE}?latitude=-33.5&longitude=151.25"
                "&current=temperature_2m,apparent_temperature,precipitation,relative_humidity_2m"
                "&timezone=Australia/Sydney",
                3,
            )
        ]

    def test_missing_current_block_maps_to_all_none_and_is_cached(self, net):
        net.queue({"hourly": {}})
        client = WeatherClient()
        expected = {"temperature": None, "feels_like": None, "precipitation": None, "humidity": None}
        assert client.get_current() == expected
        assert client.get_current() == expected
        assert len(net.calls) == 1

    @pytest.mark.parametrize("failure", [OSError("dns"), TimeoutError("slow"), b"not json", b"[1, 2]"])
    def test_failure_returns_none_and_is_not_cached(self, net, failure):
        net.queue(failure, CURRENT_PAYLOAD)
        client = WeatherClient()
        assert client.get_current() is None
        assert client.get_current()["temperature"] == 18.4
        assert len(net.calls) == 2

    def test_cache_ttl_boundary(self, net):
        net.queue(CURRENT_PAYLOAD, {"current": {"temperature_2m": 25.0}})
        client = WeatherClient()
        assert client.get_current()["temperature"] == 18.4  # fetched at t=1000
        net.now = 1000 + 599.999
        assert client.get_current()["temperature"] == 18.4  # still cached
        net.now = 1000 + 600
        assert client.get_current()["temperature"] == 25.0  # TTL is strict: age 600 refetches
        assert len(net.calls) == 2


class TestForecast:
    def test_exact_url_and_six_hour_aggregate(self, net):
        net.queue(HOURLY_PAYLOAD)
        assert WeatherClient().get_forecast() == {
            "precipitation_mm": 2.0,
            "max_temp": 19.0,
            "min_temp": 14.0,
            "avg_humidity": 63.333333333333336,
        }
        assert net.calls == [(FORECAST_URL, 8)]

    def test_window_clamps_to_available_hours(self, net):
        net.queue(HOURLY_PAYLOAD)
        assert WeatherClient().get_forecast(hours=48) == {
            "precipitation_mm": 8.0,
            "max_temp": 30.0,
            "min_temp": 14.0,
            "avg_humidity": 50.0,
        }

    def test_window_is_sized_by_precipitation_length_only(self, net):
        net.queue({"hourly": {"precipitation": [1.0, 2.0, 3.0], "temperature_2m": [20.0], "relative_humidity_2m": []}})
        assert WeatherClient().get_forecast(hours=3) == {
            "precipitation_mm": 6.0,
            "max_temp": 20.0,
            "min_temp": 20.0,
            "avg_humidity": None,
        }

    @pytest.mark.parametrize(
        ("payload", "hours"),
        [({"hourly": {"precipitation": []}}, 6), ({}, 6), (HOURLY_PAYLOAD, 0)],
    )
    def test_empty_window_returns_none_and_is_not_cached(self, net, payload, hours):
        net.queue(payload, HOURLY_PAYLOAD)
        client = WeatherClient()
        assert client.get_forecast(hours=hours) is None
        assert client.get_forecast()["precipitation_mm"] == 2.0
        assert len(net.calls) == 2

    def test_negative_hours_slice_from_the_end(self, net):
        """``n = min(hours, len)`` is negative, so the window is "all but the last |hours| entries"."""
        net.queue(HOURLY_PAYLOAD)
        assert WeatherClient().get_forecast(hours=-1) == {
            "precipitation_mm": 5.0,
            "max_temp": 30.0,
            "min_temp": 14.0,
            "avg_humidity": 55.714285714285715,
        }

    @pytest.mark.parametrize("failure", [OSError("reset"), b"{", b'{"hourly": {"precipitation": ["x"]}}'])
    def test_failure_returns_none(self, net, failure):
        net.queue(failure)
        assert WeatherClient().get_forecast() is None

    def test_cache_ttl_boundary_and_independent_caches(self, net):
        net.queue(HOURLY_PAYLOAD, CURRENT_PAYLOAD, {"hourly": {"precipitation": [9.0]}})
        client = WeatherClient()
        client.get_forecast()
        client.get_current()  # separate cache: forecast cache does not serve current
        net.now = 1599.0
        assert client.get_forecast()["precipitation_mm"] == 2.0
        net.now = 1600.0
        assert client.get_forecast()["precipitation_mm"] == 9.0
        assert [url for url, _ in net.calls] == [FORECAST_URL, CURRENT_URL, FORECAST_URL]

    def test_forecast_cache_ignores_hours_current_behavior(self, net):
        """Pins current (buggy) behavior: the forecast cache is not keyed by ``hours`` — see REFACTOR_NOTES.md.

        Within the TTL, ``get_forecast(hours=2)`` returns the aggregate computed for
        the earlier ``get_forecast(hours=6)`` call, without a fetch.
        """
        net.queue(HOURLY_PAYLOAD)
        client = WeatherClient()
        six = client.get_forecast(hours=6)
        assert client.get_forecast(hours=2) == six
        assert len(net.calls) == 1


def test_app_wires_settings_location_and_preference_timezone(clean_env):
    """``create_app`` builds the client from ``weather_lat``/``weather_lon`` and the startup timezone."""
    from golden import install_offline_weather

    from .conftest import _make_stubbed_app

    app, engine = _make_stubbed_app(bypass_auth=True, weather_lat=1.5, weather_lon=-2.25)
    try:
        client = app.state.weather_client
        assert isinstance(client, WeatherClient)
        assert (client._lat, client._lon, client._timeout, client._tz) == (1.5, -2.25, 8, "UTC")
        install_offline_weather(app)
    finally:
        engine.dispose()
