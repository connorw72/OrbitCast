"""Weather-history feature mart (CLAUDE.md §4.4, §6.2).

ERA5 archive precipitation at the label (cell, hour) pairs, with precip_lag_1h and
precip_forecast_3h derived from the hourly series (parity with serving-time
feature assembly).
"""

import math
from datetime import UTC, datetime

import h3
from orbitcast_pipelines.weather_mart import build_weather_features, parse_era5_series

_CELL = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 5))


def test_parse_era5_series_maps_hours_to_precip():
    data = {
        "hourly": {
            "time": ["2026-07-06T12:00", "2026-07-06T13:00"],
            "precipitation": [0.0, 2.5],
        }
    }
    series = parse_era5_series(data)
    assert series[datetime(2026, 7, 6, 12, tzinfo=UTC)] == 0.0
    assert series[datetime(2026, 7, 6, 13, tzinfo=UTC)] == 2.5


def test_build_weather_features_derives_lag_and_forecast():
    h11 = datetime(2026, 7, 6, 11, tzinfo=UTC)
    h12 = datetime(2026, 7, 6, 12, tzinfo=UTC)
    h15 = datetime(2026, 7, 6, 15, tzinfo=UTC)
    precip_by_cell = {_CELL: {h11: 1.0, h12: 0.0, h15: 4.0}}
    rows = build_weather_features([(_CELL, h12)], precip_by_cell)
    assert len(rows) == 1
    r = rows[0]
    assert r["h3_cell"] == _CELL
    assert r["hour_utc"] == h12
    assert math.isclose(r["precip_mm_h"], 0.0)  # current h12
    assert math.isclose(r["precip_lag_1h"], 1.0)  # h11
    assert math.isclose(r["precip_forecast_3h"], 4.0)  # h12 + 3 = h15


def test_build_weather_features_missing_hours_default_zero():
    h13 = datetime(2026, 7, 6, 13, tzinfo=UTC)
    rows = build_weather_features([(_CELL, h13)], {})  # no series for this cell
    r = rows[0]
    assert r["precip_mm_h"] == 0.0
    assert r["precip_lag_1h"] == 0.0
    assert r["precip_forecast_3h"] == 0.0
