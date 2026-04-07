"""Weather data infrastructure client."""

import json
import urllib.request


class WeatherClient:
    """Fetches current weather from Open-Meteo API."""

    def __init__(self, lat: float = 45.464, lon: float = 9.189, timeout: int = 8):
        self._lat = lat
        self._lon = lon
        self._timeout = timeout

    def get_current(self) -> dict | None:
        """Fetch current weather. Returns None on failure."""
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={self._lat}&longitude={self._lon}"
            f"&current=temperature_2m,apparent_temperature,precipitation,relative_humidity_2m"
            f"&timezone=Europe/Rome"
        )
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
                current = data.get("current", {})
                return {
                    "temperature": current.get("temperature_2m"),
                    "feels_like": current.get("apparent_temperature"),
                    "precipitation": current.get("precipitation"),
                    "humidity": current.get("relative_humidity_2m"),
                }
        except Exception:
            return None
