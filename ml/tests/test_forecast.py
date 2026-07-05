"""Serving-time forecast assembly + inference (CLAUDE.md §6.3, §7.1, §7.3).

The 48 h feature matrix must be built with exactly the FEATURE_COLUMNS ordering
the boosters were trained on (parity with the training-matrix builder). precip
lag/forecast are derived from the hourly precip series; the rolling cell median
and its `basis` come from the hierarchical fallback.
"""

import math
from datetime import UTC, datetime, timedelta

import h3
import numpy as np
from orbitcast_ml.features import FEATURE_COLUMNS, time_features
from orbitcast_ml.forecast import assemble_payload, build_feature_matrix

_CELL = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 5))
_H0 = datetime(2026, 7, 6, 12, tzinfo=UTC)


def _hours(n):
    return [_H0 + timedelta(hours=i) for i in range(n)]


def test_matrix_shape_and_column_parity():
    hours = _hours(3)
    precip = {h: 0.0 for h in hours}
    orbital = {h: (8, 70.0) for h in hours}
    x = build_feature_matrix(
        _CELL,
        hours,
        precip_by_hour=precip,
        orbital_by_hour=orbital,
        terrestrial_baseline_mbps=85.0,
        devices=42.0,
        cell_median=25.0,
        source_quality=4.0,
    )
    assert x.shape == (3, len(FEATURE_COLUMNS))
    lon = h3.cell_to_latlng(h3.int_to_str(_CELL))[1]
    lat = h3.cell_to_latlng(h3.int_to_str(_CELL))[0]
    tf = time_features(_H0, lon)
    row0 = dict(zip(FEATURE_COLUMNS, x[0], strict=True))
    assert math.isclose(row0["hour_sin"], tf["hour_sin"], abs_tol=1e-9)
    assert math.isclose(row0["local_solar_offset_h"], tf["local_solar_offset_h"], abs_tol=1e-9)
    assert math.isclose(row0["cell_lat"], lat, abs_tol=1e-6)
    assert row0["sats_visible"] == 8
    assert math.isclose(row0["cell_median_7d"], 25.0)
    assert math.isclose(row0["source_quality"], 4.0)


def test_precip_lag_and_forecast_derived_from_series():
    hours = _hours(6)
    precip = {h: 0.0 for h in hours}
    precip[hours[2]] = 3.0  # for current + lag checks
    precip[hours[5]] = 7.0  # 3h ahead of index 2, for the forecast check
    orbital = {h: (5, 40.0) for h in hours}
    x = build_feature_matrix(
        _CELL,
        hours,
        precip,
        orbital,
        terrestrial_baseline_mbps=0.0,
        devices=0.0,
        cell_median=10.0,
        source_quality=4.0,
    )
    rows = [dict(zip(FEATURE_COLUMNS, r, strict=True)) for r in x]
    # current precip at index 2
    assert math.isclose(rows[2]["precip_mm_h"], 3.0)
    # lag_1h at index 3 sees index 2's rain
    assert math.isclose(rows[3]["precip_lag_1h"], 3.0)
    # forecast_3h at index 2 looks 3 hours ahead -> index 5's rain
    assert math.isclose(rows[2]["precip_forecast_3h"], 7.0)
    # a dry hour with nothing 3h ahead
    assert math.isclose(rows[0]["precip_forecast_3h"], 0.0)


def test_assemble_payload_shape_and_content():
    hours = _hours(2)
    preds = {
        "latency": {
            0.1: np.array([20.0, 21.0]),
            0.5: np.array([30.0, 31.0]),
            0.9: np.array([45.0, 46.0]),
        },
        "dl_throughput": {
            0.1: np.array([80.0, 82.0]),
            0.5: np.array([120.0, 121.0]),
            0.9: np.array([150.0, 151.0]),
        },
    }
    weather = [{"precip_mm_h": 0.0}, {"precip_mm_h": 2.5}]
    payload = assemble_payload(hours, preds, basis="region", weather_per_hour=weather)
    assert len(payload) == 2
    first = payload[0]
    assert first["hour"] == hours[0].isoformat()
    assert first["basis"] == "region"
    assert first["latency"] == {"q10": 20.0, "q50": 30.0, "q90": 45.0}
    assert first["dl"] == {"q10": 80.0, "q50": 120.0, "q90": 150.0}
    assert first["weather"]["precip_mm_h"] == 0.0
    assert payload[1]["weather"]["precip_mm_h"] == 2.5
