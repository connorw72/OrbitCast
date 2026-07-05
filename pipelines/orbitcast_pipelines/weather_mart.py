"""Weather-history feature mart (CLAUDE.md §4.4, §6.2).

ERA5 archive precipitation at the label (cell, hour) pairs. One archive request per
cell over its label date-span keeps within Open-Meteo's courtesy ceiling; the
hourly series then yields precip_mm_h and its 1 h lag / 3 h look-ahead — the same
transform the serving path applies, so training/serving features stay aligned.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orbitcast_core.spatial import cell_centroid

ERA5_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def parse_era5_series(data: dict) -> dict[datetime, float]:
    """Map an ERA5 archive response to {UTC hour -> precip mm/h}."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    precip = hourly.get("precipitation", [])
    series: dict[datetime, float] = {}
    for iso, mm in zip(times, precip, strict=False):
        ts = datetime.fromisoformat(iso).replace(tzinfo=UTC)
        series[ts] = float(mm or 0.0)
    return series


def build_weather_features(
    cell_hours: Sequence[tuple[int, datetime]],
    precip_by_cell: Mapping[int, Mapping[datetime, float]],
) -> list[dict]:
    """One weather row per (cell, hour): current precip + 1 h lag + 3 h look-ahead.

    Missing hours read as 0 (dry), matching the serving-time assembly.
    """
    rows: list[dict] = []
    for cell, hour in cell_hours:
        series = precip_by_cell.get(cell, {})
        rows.append(
            {
                "h3_cell": cell,
                "hour_utc": hour,
                "precip_mm_h": float(series.get(hour, 0.0)),
                "precip_lag_1h": float(series.get(hour - timedelta(hours=1), 0.0)),
                "precip_forecast_3h": float(series.get(hour + timedelta(hours=3), 0.0)),
            }
        )
    return rows


def _http_fetch(url: str, params: dict) -> dict:
    import httpx

    resp = httpx.get(url, params=params, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def fetch_era5_precip(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    fetch: Callable[[str, dict], dict] = _http_fetch,
) -> dict[datetime, float]:
    """Hourly ERA5 precipitation series for a location over [start_date, end_date]
    (YYYY-MM-DD). Empty dict on failure (weather degrades to zero-precip)."""
    try:
        data = fetch(
            ERA5_ARCHIVE_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "start_date": start_date,
                "end_date": end_date,
                "hourly": "precipitation",
            },
        )
    except Exception:
        return {}
    return parse_era5_series(data)


def fetch_precip_by_cell(
    cell_hours: Sequence[tuple[int, datetime]],
    fetch: Callable[[str, dict], dict] = _http_fetch,
) -> dict[int, dict[datetime, float]]:
    """Fetch one ERA5 series per distinct cell, spanning that cell's label hours.

    The 1 h lag / 3 h look-ahead need neighbours of each label hour, so each cell's
    fetch window is padded a day on either side.
    """
    by_cell: dict[int, list[datetime]] = {}
    for cell, hour in cell_hours:
        by_cell.setdefault(cell, []).append(hour)

    out: dict[int, dict[datetime, float]] = {}
    for cell, hours in by_cell.items():
        lat, lon = cell_centroid(cell)
        start = (min(hours) - timedelta(days=1)).date().isoformat()
        end = (max(hours) + timedelta(days=1)).date().isoformat()
        out[cell] = fetch_era5_precip(lat, lon, start, end, fetch=fetch)
    return out


def build_weather_mart_from_marts(
    marts_dir: Path,
    fetch: Callable[[str, dict], dict] = _http_fetch,
) -> list[dict]:
    """Assemble the weather mart from the label (cell, hour) pairs on disk."""
    from .orbital_mart import label_cell_hours

    cell_hours = label_cell_hours(marts_dir)
    if not cell_hours:
        return []
    precip_by_cell = fetch_precip_by_cell(cell_hours, fetch=fetch)
    return build_weather_features(cell_hours, precip_by_cell)
