"""Forecast service composition (CLAUDE.md §7.1, §7.3).

`build_forecast` glues live providers -> the ml feature matrix -> in-process
inference -> the API payload. Tested with a fake model + injected providers so the
wiring is pinned without network calls or LightGBM training.
"""

from datetime import UTC, datetime
from typing import cast

import h3
import numpy as np
from orbitcast_api.forecast import build_forecast, next_hours
from orbitcast_ml.models import ForecastModel

_CELL = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 5))


class _FakeModel:
    """Returns deterministic per-hour quantiles sized to the input matrix."""

    def predict(self, x):
        n = x.shape[0]
        return {
            "latency": {0.1: np.full(n, 20.0), 0.5: np.full(n, 30.0), 0.9: np.full(n, 45.0)},
            "dl_throughput": {
                0.1: np.full(n, 80.0),
                0.5: np.full(n, 120.0),
                0.9: np.full(n, 150.0),
            },
        }


def test_next_hours_truncates_to_hour_and_is_consecutive():
    hours = next_hours(datetime(2026, 7, 6, 12, 30, tzinfo=UTC), n=48)
    assert len(hours) == 48
    assert hours[0] == datetime(2026, 7, 6, 12, tzinfo=UTC)
    assert hours[1] == datetime(2026, 7, 6, 13, tzinfo=UTC)


def test_build_forecast_produces_48h_payload_with_basis():
    now = datetime(2026, 7, 6, 12, 30, tzinfo=UTC)
    payload = build_forecast(
        cell=_CELL,
        now=now,
        model=cast(ForecastModel, _FakeModel()),
        weather_series={},
        orbital_by_hour={},
        ookla_baseline=80.0,
        ookla_devices=40.0,
        cell_median=25.0,
        basis="region",
    )
    assert len(payload) == 48
    first = payload[0]
    assert set(first) == {"hour", "basis", "latency", "dl", "weather"}
    assert first["basis"] == "region"
    assert first["hour"] == "2026-07-06T12:00:00+00:00"
    assert first["latency"] == {"q10": 20.0, "q50": 30.0, "q90": 45.0}
    assert first["dl"] == {"q10": 80.0, "q50": 120.0, "q90": 150.0}


def test_build_forecast_threads_weather_into_payload():
    now = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    rain_hour = datetime(2026, 7, 6, 15, tzinfo=UTC)
    payload = build_forecast(
        cell=_CELL,
        now=now,
        model=cast(ForecastModel, _FakeModel()),
        weather_series={rain_hour: 4.2},
        orbital_by_hour={},
        ookla_baseline=0.0,
        ookla_devices=0.0,
        cell_median=10.0,
        basis="cell",
    )
    # index 3 == 15:00, the rainy hour
    assert payload[3]["weather"]["precip_mm_h"] == 4.2
    assert payload[0]["weather"]["precip_mm_h"] == 0.0
