"""Training-matrix builder (CLAUDE.md §5.4, §6.2, Phase 3).

Deterministic DuckDB SQL that fuses the warehouse marts into one row per
(cell, hour, target): all FEATURE_COLUMNS + the label + a source-quality weight.
Tested against fixture tables so the fusion logic is pinned without needing live
ingestion. The SQL-computed time features must match the Python `time_features`
used at serving time — a mismatch would silently skew predictions.
"""

import math
from datetime import UTC, datetime

import duckdb
import h3
import pytest
from orbitcast_ml.features import FEATURE_COLUMNS, time_features
from orbitcast_ml.training_matrix import SOURCE_QUALITY, build_training_matrix, to_arrays

_CELL = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 5))
_MON = datetime(2026, 7, 6, 12, tzinfo=UTC)  # Monday 12:00 UTC
_TUE = datetime(2026, 7, 7, 12, tzinfo=UTC)
_WED = datetime(2026, 7, 8, 12, tzinfo=UTC)


@pytest.fixture
def warehouse():
    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    con.execute("SET TimeZone='UTC';")
    con.execute(
        "CREATE TABLE labels(h3_cell BIGINT, hour_utc TIMESTAMP, target VARCHAR, "
        "value DOUBLE, source VARCHAR, samples INTEGER)"
    )
    con.executemany(
        "INSERT INTO labels VALUES (?,?,?,?,?,?)",
        [
            (_CELL, _MON, "latency", 30.0, "atlas", 5),
            (_CELL, _TUE, "latency", 40.0, "atlas", 5),
            (_CELL, _WED, "latency", 50.0, "atlas", 5),
            (_CELL, _MON, "dl_throughput", 120.0, "mlab", 9),
        ],
    )
    con.execute(
        "CREATE TABLE orbital_features(h3_cell BIGINT, hour_utc TIMESTAMP, "
        "sats_visible INTEGER, max_elevation_deg DOUBLE)"
    )
    con.executemany(
        "INSERT INTO orbital_features VALUES (?,?,?,?)",
        [
            (_CELL, _MON, 8, 72.0),
            (_CELL, _TUE, 7, 60.0),
            (_CELL, _WED, 9, 81.0),
        ],
    )
    con.execute(
        "CREATE TABLE weather_features(h3_cell BIGINT, hour_utc TIMESTAMP, "
        "precip_mm_h DOUBLE, precip_lag_1h DOUBLE, precip_forecast_3h DOUBLE)"
    )
    con.executemany(
        "INSERT INTO weather_features VALUES (?,?,?,?,?)",
        [
            (_CELL, _MON, 0.0, 0.0, 1.2),
            (_CELL, _TUE, 0.5, 0.0, 0.0),
            (_CELL, _WED, 0.0, 0.5, 0.0),
        ],
    )
    con.execute(
        "CREATE TABLE ookla_context(h3_cell BIGINT, tests INTEGER, devices INTEGER, "
        "terrestrial_baseline_mbps DOUBLE, terrestrial_latency_ms DOUBLE)"
    )
    con.execute("INSERT INTO ookla_context VALUES (?, 100, 42, 85.0, 18.0)", [_CELL])
    return con


def _row(rows, target, hour):
    hour = hour.replace(tzinfo=None)
    return next(r for r in rows if r["target"] == target and r["hour_utc"] == hour)


def test_one_row_per_label(warehouse):
    rows = build_training_matrix(warehouse)
    assert len(rows) == 4
    assert {r["target"] for r in rows} == {"latency", "dl_throughput"}


def test_features_are_joined_through(warehouse):
    rows = build_training_matrix(warehouse)
    wed = _row(rows, "latency", _WED)
    assert wed["label"] == 50.0
    assert wed["sats_visible"] == 9
    assert math.isclose(wed["max_elevation_deg"], 81.0)
    assert math.isclose(wed["precip_lag_1h"], 0.5)
    assert math.isclose(wed["devices"], 42.0)
    assert math.isclose(wed["terrestrial_baseline_mbps"], 85.0)
    assert math.isclose(wed["cell_lat"], h3.cell_to_latlng(h3.int_to_str(_CELL))[0], abs_tol=1e-6)


def test_source_quality_weight(warehouse):
    rows = build_training_matrix(warehouse)
    assert _row(rows, "latency", _MON)["source_quality"] == SOURCE_QUALITY["atlas"]
    assert _row(rows, "dl_throughput", _MON)["source_quality"] == SOURCE_QUALITY["mlab"]


def test_sql_time_features_match_python(warehouse):
    rows = build_training_matrix(warehouse)
    mon = _row(rows, "latency", _MON)
    lon = h3.cell_to_latlng(h3.int_to_str(_CELL))[1]
    expected = time_features(_MON, lon)
    assert math.isclose(mon["hour_sin"], expected["hour_sin"], abs_tol=1e-9)
    assert math.isclose(mon["hour_cos"], expected["hour_cos"], abs_tol=1e-9)
    assert mon["day_of_week"] == expected["day_of_week"] == 0.0  # Monday
    assert math.isclose(mon["local_solar_offset_h"], expected["local_solar_offset_h"], abs_tol=1e-9)


def test_rolling_7day_median_excludes_current_and_starts_null(warehouse):
    rows = build_training_matrix(warehouse)
    # First latency observation has no prior 7-day history.
    assert _row(rows, "latency", _MON)["cell_median_7d"] is None
    # Wednesday sees Monday(30) + Tuesday(40) within the trailing week -> median 35.
    assert math.isclose(_row(rows, "latency", _WED)["cell_median_7d"], 35.0)


def test_to_arrays_yields_feature_matrix_and_weights(warehouse):
    rows = build_training_matrix(warehouse)
    x, y, w = to_arrays(rows, "latency")
    assert x.shape == (3, len(FEATURE_COLUMNS))
    assert len(y) == 3
    # weights follow source quality (all atlas here)
    assert all(weight == SOURCE_QUALITY["atlas"] for weight in w)
