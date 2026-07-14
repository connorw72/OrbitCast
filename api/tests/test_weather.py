"""Open-Meteo now-cast with a per-(cell, hour) cache (CLAUDE.md §4.4).

A cell's forecast is fetched at most hourly regardless of visitor volume, and we
request the cell centroid, never per-user coordinates (D12). Weather is optional:
a fetch failure must not break the sky view.
"""

from datetime import UTC, datetime

from orbitcast_api.weather import get_forecast_series_cached, get_nowcast, parse_current

_CANNED = {
    "current": {
        "time": "2026-07-04T12:00",
        "precipitation": 0.5,
        "snowfall": 0.0,
        "cloud_cover": 40,
    }
}


class _Fetch:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def __call__(self, lat: float, lon: float) -> dict:
        self.calls += 1
        return self.payload


def test_parses_current_block_into_weathernow() -> None:
    w = parse_current(_CANNED)
    assert w.precip_mm_h == 0.5
    assert w.cloud_cover_pct == 40.0
    assert w.snow_mm_h == 0.0


def test_same_cell_and_hour_is_fetched_only_once() -> None:
    fetch = _Fetch(_CANNED)
    now = datetime(2026, 7, 4, 12, 5, tzinfo=UTC)
    first = get_nowcast(1001, 47.6, -122.3, now, fetch=fetch)
    second = get_nowcast(1001, 47.6, -122.3, now.replace(minute=55), fetch=fetch)
    assert fetch.calls == 1
    assert first == second


def test_new_hour_refetches() -> None:
    fetch = _Fetch(_CANNED)
    get_nowcast(1002, 47.6, -122.3, datetime(2026, 7, 4, 12, 5, tzinfo=UTC), fetch=fetch)
    get_nowcast(1002, 47.6, -122.3, datetime(2026, 7, 4, 13, 5, tzinfo=UTC), fetch=fetch)
    assert fetch.calls == 2


def test_fetch_failure_returns_none_and_does_not_raise() -> None:
    def boom(lat: float, lon: float) -> dict:
        raise RuntimeError("open-meteo down")

    now = datetime(2026, 7, 4, 12, 5, tzinfo=UTC)
    assert get_nowcast(1003, 47.6, -122.3, now, fetch=boom) is None


# The 48 h series feeds /v1/forecast and (per active cell) /v1/map, so it gets the
# same per-(cell, hour) posture as the now-cast — without it, every forecast
# request is an Open-Meteo round-trip (design doc Part 1b freshness argument).

_SERIES = {
    "hourly": {"time": ["2026-07-04T12:00", "2026-07-04T13:00"], "precipitation": [0.0, 1.2]}
}


def test_series_same_cell_and_hour_is_fetched_only_once() -> None:
    fetch = _Fetch(_SERIES)
    now = datetime(2026, 7, 4, 12, 5, tzinfo=UTC)
    first = get_forecast_series_cached(2001, 47.6, -122.3, now, fetch=fetch)
    second = get_forecast_series_cached(2001, 47.6, -122.3, now.replace(minute=50), fetch=fetch)
    assert fetch.calls == 1
    assert first == second
    assert first[datetime(2026, 7, 4, 13, tzinfo=UTC)] == 1.2


def test_series_new_hour_refetches() -> None:
    fetch = _Fetch(_SERIES)
    for hour in (12, 13):
        now = datetime(2026, 7, 4, hour, 5, tzinfo=UTC)
        get_forecast_series_cached(2002, 47.6, -122.3, now, fetch=fetch)
    assert fetch.calls == 2


def test_series_failure_returns_empty_and_is_not_cached() -> None:
    calls = {"n": 0}

    def flaky(lat: float, lon: float) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("open-meteo down")
        return _SERIES

    now = datetime(2026, 7, 4, 12, 5, tzinfo=UTC)
    assert get_forecast_series_cached(2003, 47.6, -122.3, now, fetch=flaky) == {}
    assert get_forecast_series_cached(2003, 47.6, -122.3, now, fetch=flaky) != {}
    assert calls["n"] == 2
