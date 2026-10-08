"""get_weather(location) — current weather, with a 3-level fallback chain:

1. OpenWeatherMap  (only if OPENWEATHER_API_KEY is set in .env)
2. Open-Meteo      (free, no API key — the default path)
3. Playwright browser search (last resort if both APIs fail)
"""

import logging

import httpx

from app.config import settings
from app.tools import browser_tool

logger = logging.getLogger(__name__)

# WMO weather codes used by Open-Meteo
_WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


async def get_weather(location: str) -> str:
    """Return a human-readable current-weather report for `location`."""
    if settings.openweather_api_key:
        try:
            return await _openweathermap(location)
        except Exception as exc:  # noqa: BLE001 — fall through to next provider
            logger.warning("OpenWeatherMap failed (%s), falling back to Open-Meteo", exc)

    try:
        return await _open_meteo(location)
    except Exception as exc:  # noqa: BLE001 — fall through to browser search
        logger.warning("Open-Meteo failed (%s), falling back to browser search", exc)

    return await browser_tool.browser_search(f"current weather in {location} right now")


async def _openweathermap(location: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": location, "appid": settings.openweather_api_key, "units": "metric"},
        )
        resp.raise_for_status()
        data = resp.json()

    return (
        f"Current weather in {data['name']}, {data['sys'].get('country', '')}: "
        f"{data['weather'][0]['description']}, {data['main']['temp']:.1f}°C "
        f"(feels like {data['main']['feels_like']:.1f}°C), "
        f"humidity {data['main']['humidity']}%, "
        f"wind {data['wind']['speed']} m/s. (Source: OpenWeatherMap)"
    )


async def _open_meteo(location: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        # Step 1: geocode the location name to coordinates
        geo = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "format": "json"},
        )
        geo.raise_for_status()
        results = geo.json().get("results")
        if not results:
            return f"Could not find a place named '{location}'. Try a nearby larger city."
        place = results[0]

        # Step 2: fetch current conditions
        weather = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                           "weather_code,wind_speed_10m",
                "timezone": "auto",
            },
        )
        weather.raise_for_status()
        current = weather.json()["current"]

    condition = _WMO_CODES.get(current.get("weather_code"), "unknown conditions")
    place_name = ", ".join(
        p for p in [place.get("name"), place.get("admin1"), place.get("country")] if p
    )
    return (
        f"Current weather in {place_name}: {condition}, "
        f"{current['temperature_2m']}°C (feels like {current['apparent_temperature']}°C), "
        f"humidity {current['relative_humidity_2m']}%, "
        f"wind {current['wind_speed_10m']} km/h. (Source: Open-Meteo)"
    )
