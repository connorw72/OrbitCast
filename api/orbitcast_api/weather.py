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


def _http_fetch_series(lat: float, lon: float) -> dict:
    import httpx

    resp = httpx.get(
        OPEN_METEO_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "precipitation",
            "forecast_days": 2,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def parse_precip_series(data: dict) -> dict[datetime, float]:
    """Map Open-Meteo hourly output to {UTC hour -> precip mm/h}."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    precip = hourly.get("precipitation", [])
    series: dict[datetime, float] = {}
    for iso, mm in zip(times, precip, strict=False):
        # Open-Meteo returns naive local-to-UTC ISO strings; we request default UTC.
        ts = datetime.fromisoformat(iso).replace(tzinfo=UTC)
        series[ts] = float(mm or 0.0)
    return series


def get_forecast_series(
    lat: float,
    lon: float,
    fetch: Callable[[float, float], dict] = _http_fetch_series,
) -> dict[datetime, float]:
    """48 h hourly precipitation series for a location. Empty dict if unavailable
    (the forecast degrades to zero-precip features rather than failing)."""
    try:
        data = fetch(lat, lon)
    except Exception:
        return {}
    return parse_precip_series(data)


# 48 h series cache, same per-(cell, hour) courtesy posture as the now-cast. All
# live keys share the current hour, so the whole dict is dropped on rollover
# rather than growing one generation per hour.
_series_cache: dict[tuple[int, datetime], dict[datetime, float]] = {}


def get_forecast_series_cached(
    cell: int,
    lat: float,
    lon: float,
    now: datetime,
    fetch: Callable[[float, float], dict] = _http_fetch_series,
) -> dict[datetime, float]:
    """48 h precip series for a cell, fetched at most once per UTC hour.

    Failures return {} and are not cached, so the next request retries."""
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    key = (cell, hour)
    hit = _series_cache.get(key)
    if hit is not None:
        return hit
    if any(k[1] != hour for k in _series_cache):
        _series_cache.clear()
    series = get_forecast_series(lat, lon, fetch)
    if series:
        _series_cache[key] = series
    return series


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
