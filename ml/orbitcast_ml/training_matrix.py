"""Training-matrix builder (CLAUDE.md §5.4, §6.2, Phase 3).

Deterministic DuckDB SQL that fuses the warehouse marts into one row per
(cell, hour, target): the full FEATURE_COLUMNS vector, the label, and a
source-quality weight (user > atlas > mlab). Determinism given warehouse state is
the contract — the same warehouse always yields the same matrix, so training runs
are reproducible.

Time/geo features are computed in SQL here and in Python (`features.time_features`)
at serving time; a parity test pins the two implementations together.
"""

from collections.abc import Sequence

import duckdb
import numpy as np
from numpy.typing import NDArray

from .features import FEATURE_COLUMNS

# Sample-quality weights and the data-source-indicator feature value (§6.2, §6.4).
SOURCE_QUALITY: dict[str, float] = {"user": 4.0, "atlas": 2.0, "mlab": 1.0}


def _source_quality_case(column: str) -> str:
    whens = " ".join(f"WHEN '{src}' THEN {q}" for src, q in SOURCE_QUALITY.items())
    return f"CASE {column} {whens} ELSE 1.0 END"


def training_matrix_sql(
    labels: str = "labels",
    orbital: str = "orbital_features",
    weather: str = "weather_features",
    ookla: str = "ookla_context",
) -> str:
    """The training-matrix SELECT. Inputs are warehouse table/view names."""
    return f"""
    WITH base AS (
        SELECT
            l.h3_cell,
            l.hour_utc,
            l.target,
            l.value AS label,
            l.source,
            h3_cell_to_lat(l.h3_cell) AS cell_lat,
            h3_cell_to_lng(l.h3_cell) AS cell_lon,
            hour(l.hour_utc) + minute(l.hour_utc) / 60.0
                + second(l.hour_utc) / 3600.0 AS decimal_hour,
            (isodow(l.hour_utc) - 1)::DOUBLE AS day_of_week,
            quantile_cont(l.value, 0.5) OVER (
                PARTITION BY l.h3_cell, l.target
                ORDER BY epoch(l.hour_utc)
                RANGE BETWEEN 604800 PRECEDING AND 3600 PRECEDING
            ) AS cell_median_7d
        FROM {labels} l
    )
    SELECT
        b.h3_cell,
        b.hour_utc,
        b.target,
        b.source,
        b.label,
        sin(2 * pi() * b.decimal_hour / 24) AS hour_sin,
        cos(2 * pi() * b.decimal_hour / 24) AS hour_cos,
        b.day_of_week,
        b.cell_lon / 15.0 AS local_solar_offset_h,
        w.precip_mm_h,
        w.precip_lag_1h,
        w.precip_forecast_3h,
        o.sats_visible,
        o.max_elevation_deg,
        b.cell_lat,
        ok.terrestrial_baseline_mbps,
        ok.devices,
        b.cell_median_7d,
        {_source_quality_case("b.source")} AS source_quality
    FROM base b
    LEFT JOIN {orbital} o ON o.h3_cell = b.h3_cell AND o.hour_utc = b.hour_utc
    LEFT JOIN {weather} w ON w.h3_cell = b.h3_cell AND w.hour_utc = b.hour_utc
    LEFT JOIN {ookla} ok ON ok.h3_cell = b.h3_cell
    ORDER BY b.target, b.h3_cell, b.hour_utc
    """


def build_training_matrix(
    con: duckdb.DuckDBPyConnection,
    labels: str = "labels",
    orbital: str = "orbital_features",
    weather: str = "weather_features",
    ookla: str = "ookla_context",
) -> list[dict]:
    """Run the training-matrix SQL and return one dict per (cell, hour, target)."""
    cur = con.execute(training_matrix_sql(labels, orbital, weather, ookla))
    columns = [c[0] for c in cur.description]
    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def to_arrays(
    rows: Sequence[dict], target: str
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Split matrix rows for one target into (X, y, sample_weight).

    Missing feature values become NaN (LightGBM treats NaN as missing, §6.3).
    """
    selected = [r for r in rows if r["target"] == target]
    x = np.array([[_nan(r[col]) for col in FEATURE_COLUMNS] for r in selected], dtype=float)
    if x.size == 0:
        # No rows for this target: keep X 2-D as (0, n_features) so downstream
        # LightGBM predict (which rejects 1-D input) still accepts it.
        x = x.reshape(0, len(FEATURE_COLUMNS))
    y = np.array([r["label"] for r in selected], dtype=float)
    w = np.array([r["source_quality"] for r in selected], dtype=float)
    return x, y, w


def _nan(value: object) -> float:
    return float("nan") if value is None else float(value)  # type: ignore[arg-type]
