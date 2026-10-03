"""Weather data infrastructure client."""

import json
import logging
import time
import urllib.request
from typing import TypedDict

from greenhouse_core.constants import DEFAULT_LATITUDE, DEFAULT_LONGITUDE, WEATHER_FORECAST_CACHE_TTL_SECONDS

logger = logging.getLogger(__name__)

_FORECAST_CACHE_TTL = WEATHER_FORECAST_CACHE_TTL_SECONDS  # module-level so tests can patch the TTL


class CurrentWeather(TypedDict):
    """``WeatherClient.get_current`` result: Open-Meteo's current values (each ``None`` when absent)."""

    temperature: float | None
    feels_like: float | None
    precipitation: float | None
    humidity: float | None


class WeatherForecast(TypedDict):
    """``WeatherClient.get_forecast`` result: the next-N-hours window aggregated."""

    precipitation_mm: float
    max_temp: float | None
    min_temp: float | None
    avg_humidity: float | None


class WeatherClient:
    """Fetches current weather from Open-Meteo API."""

    def __init__(
        self, lat: float = DEFAULT_LATITUDE, lon: float = DEFAULT_LONGITUDE, timeout: int = 8, tz: str = "UTC"
    ):
        self._lat = lat
        self._lon = lon
        self._timeout = timeout
        # Open-Meteo localizes its hourly timestamps to this zone. It must match
        # the single authoritative tz (UserPreferences.timezone) so "next 6
        # hours" aligns with the clock the engine reasons in, not a hardcoded
        # Europe/Rome.
        self._tz = tz
        self._get_current_cache: tuple[float, CurrentWeather] | None = None
        self._get_forecast_cache: tuple[float, WeatherForecast] | None = None

    def get_current(self) -> CurrentWeather | None:
        """Fetch current weather. Returns None on failure."""
        if self._get_current_cache is not None:
            cached_at, cached_value = self._get_current_cache
            if time.monotonic() - cached_at < _FORECAST_CACHE_TTL:
                return cached_value

        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={self._lat}&longitude={self._lon}"
            f"&current=temperature_2m,apparent_temperature,precipitation,relative_humidity_2m"
            f"&timezone={self._tz}"
        )
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
                current = data.get("current", {})
                result: CurrentWeather = {
                    "temperature": current.get("temperature_2m"),
                    "feels_like": current.get("apparent_temperature"),
                    "precipitation": current.get("precipitation"),
                    "humidity": current.get("relative_humidity_2m"),
                }
                self._get_current_cache = (time.monotonic(), result)
                return result
        except Exception:
            logger.debug("Open-Meteo current-weather request failed", exc_info=True)
            return None

    def get_forecast(self, hours: int = 6) -> WeatherForecast | None:
        """Fetch aggregated weather forecast for the next N hours.

        Returns precipitation sum, max/min temperature, and average humidity
        across the requested window, or None on network error.
        """
        if self._get_forecast_cache is not None:
            cached_at, cached_value = self._get_forecast_cache
            if time.monotonic() - cached_at < _FORECAST_CACHE_TTL:
                return cached_value

        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={self._lat}&longitude={self._lon}"
            f"&hourly=precipitation,temperature_2m,relative_humidity_2m"
            f"&forecast_days=2"
            f"&timezone={self._tz}"
        )
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                data = json.loads(resp.read())

            hourly = data.get("hourly", {})
            precip_list: list[float] = hourly.get("precipitation", [])
            temp_list: list[float] = hourly.get("temperature_2m", [])
            humidity_list: list[float] = hourly.get("relative_humidity_2m", [])

            n = min(hours, len(precip_list))
            if n == 0:
                return None

            window_precip = precip_list[:n]
            window_temp = temp_list[:n]
            window_humidity = humidity_list[:n]

            result: WeatherForecast = {
                "precipitation_mm": sum(window_precip),
                "max_temp": max(window_temp) if window_temp else None,
                "min_temp": min(window_temp) if window_temp else None,
                "avg_humidity": sum(window_humidity) / len(window_humidity) if window_humidity else None,
            }
            self._get_forecast_cache = (time.monotonic(), result)
            return result
        except Exception:
            logger.debug("Open-Meteo forecast request failed", exc_info=True)
            return None
