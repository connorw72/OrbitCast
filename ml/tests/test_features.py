"""Feature engineering (CLAUDE.md §6.2). The FEATURE_COLUMNS ordering is the
single source of truth shared by the training-matrix builder and serving-time
inference — a mismatch silently corrupts predictions, so it is pinned here."""

import math
from datetime import UTC, datetime

from orbitcast_ml.features import FEATURE_COLUMNS, time_features


def test_hour_of_day_is_cyclical_utc():
    # Midnight UTC: sin(0)=0, cos(0)=1.
    f = time_features(datetime(2026, 7, 5, 0, tzinfo=UTC), lon=0.0)
    assert math.isclose(f["hour_sin"], 0.0, abs_tol=1e-9)
    assert math.isclose(f["hour_cos"], 1.0, abs_tol=1e-9)
    # 06:00 UTC is a quarter turn: sin=1, cos=0.
    f6 = time_features(datetime(2026, 7, 5, 6, tzinfo=UTC), lon=0.0)
    assert math.isclose(f6["hour_sin"], 1.0, abs_tol=1e-9)
    assert math.isclose(f6["hour_cos"], 0.0, abs_tol=1e-9)


def test_hour_includes_fractional_minutes():
    # 12:30 -> 12.5/24 of a turn.
    f = time_features(datetime(2026, 7, 5, 12, 30, tzinfo=UTC), lon=0.0)
    angle = 2 * math.pi * 12.5 / 24
    assert math.isclose(f["hour_sin"], math.sin(angle), abs_tol=1e-9)
    assert math.isclose(f["hour_cos"], math.cos(angle), abs_tol=1e-9)


def test_day_of_week_monday_is_zero():
    # 2026-07-06 is a Monday.
    f = time_features(datetime(2026, 7, 6, 12, tzinfo=UTC), lon=0.0)
    assert f["day_of_week"] == 0
    # 2026-07-05 is a Sunday.
    assert time_features(datetime(2026, 7, 5, 12, tzinfo=UTC), lon=0.0)["day_of_week"] == 6


def test_local_solar_offset_is_lon_over_15():
    def offset(lon):
        return time_features(datetime(2026, 7, 5, tzinfo=UTC), lon)["local_solar_offset_h"]

    assert math.isclose(offset(0.0), 0.0)
    assert math.isclose(offset(15.0), 1.0)
    assert math.isclose(offset(-30.0), -2.0)


def test_naive_datetime_treated_as_utc():
    naive = time_features(datetime(2026, 7, 5, 6), lon=0.0)
    aware = time_features(datetime(2026, 7, 5, 6, tzinfo=UTC), lon=0.0)
    assert naive == aware


def test_feature_columns_is_pinned_and_ordered():
    # Exact ordering matters — LightGBM consumes a positional feature vector.
    assert FEATURE_COLUMNS == [
        "hour_sin",
        "hour_cos",
        "day_of_week",
        "local_solar_offset_h",
        "precip_mm_h",
        "precip_lag_1h",
        "precip_forecast_3h",
        "sats_visible",
        "max_elevation_deg",
        "cell_lat",
        "terrestrial_baseline_mbps",
        "devices",
        "cell_median_7d",
        "source_quality",
    ]
