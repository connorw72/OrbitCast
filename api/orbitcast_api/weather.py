"""Open-Meteo now-cast client with a per-(cell, hour) cache (CLAUDE.md D8, §4.4).

No API key; free non-commercial tier. To respect the courtesy ceiling we cache by
(H3 cell, UTC hour): a cell's weather is fetched at most once per hour regardless
of visitor volume. Weather is best-effort context for the sky view — if Open-Meteo
is unavailable we return None rather than failing the request.

Phase 1 keeps this cache in-process. It migrates to the Postgres `weather_cache`
table (§7.2) in Phase 2 when the serving DB layer is built, so the hourly budget
holds across processes/instances.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from .schemas import WeatherNow

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_CURRENT_VARS = "precipitation,snowfall,cloud_cover"

_cache: dict[tuple[int, datetime], WeatherNow] = {}


def _http_fetch(lat: float, lon: float) -> dict:
    import httpx

    resp = httpx.get(
        OPEN_METEO_URL,
        params={"latitude": lat, "longitude": lon, "current": _CURRENT_VARS},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def parse_current(data: dict) -> WeatherNow:
    current = data["current"]
    return WeatherNow(
        precip_mm_h=float(current.get("precipitation", 0.0)),
        cloud_cover_pct=float(current.get("cloud_cover", 0.0)),
        snow_mm_h=float(current.get("snowfall", 0.0)),
    )


def get_nowcast(
    cell: int,
    lat: float,
    lon: float,
    now: datetime,
    fetch: Callable[[float, float], dict] = _http_fetch,
) -> WeatherNow | None:
    """Current weather for a cell, cached per UTC hour. None if unavailable."""
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    key = (cell, hour)
    if key in _cache:
        return _cache[key]
    try:
        data = fetch(lat, lon)
    except Exception:
        return None
    weather = parse_current(data)
    _cache[key] = weather
    return weather
