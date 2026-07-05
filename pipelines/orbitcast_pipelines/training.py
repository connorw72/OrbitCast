"""train_models input assembly + runner (CLAUDE.md §5.5, §6.4).

Fuses the label marts into the long-form ``labels`` table the training-matrix
builder expects, and registers the feature inputs (orbital, weather, Ookla) —
creating empty tables where an ingest doesn't exist yet — so the fusion SQL always
runs. `run_train_models` then builds the matrix and hands it to the promotion-gated
training run (`orbitcast_ml.registry.run_training`).
"""

from pathlib import Path

import duckdb
from orbitcast_ml.registry import run_training
from orbitcast_ml.train import adaptive_cutoff
from orbitcast_ml.training_matrix import build_training_matrix


def _register_or_empty(
    con: duckdb.DuckDBPyConnection, name: str, path: Path, empty_ddl: str
) -> None:
    con.execute(f"DROP TABLE IF EXISTS {name}")
    if path.exists():
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{path}')")
    else:
        con.execute(empty_ddl)


def assemble_training_inputs(con: duckdb.DuckDBPyConnection, marts_dir: Path) -> int:
    """Build the ``labels`` table + feature-input tables from the marts.

    Returns the number of label rows assembled. Currently sources labels from the
    RIPE Atlas latency mart; M-Lab throughput labels join in once that ingest lands
    (docs/mlab-setup.md). Returns 0 when no label mart exists yet.
    """
    marts_dir = Path(marts_dir)
    con.execute("DROP TABLE IF EXISTS labels")
    con.execute(
        "CREATE TABLE labels(h3_cell BIGINT, hour_utc TIMESTAMP, target VARCHAR, "
        "value DOUBLE, source VARCHAR, samples INTEGER)"
    )

    atlas = marts_dir / "atlas_latency_hourly.parquet"
    if atlas.exists():
        con.execute(
            "INSERT INTO labels SELECT h3_cell, hour_utc, 'latency', rtt_ms_median, "
            f"'atlas', samples FROM read_parquet('{atlas}')"
        )

    _register_or_empty(
        con,
        "orbital_features",
        marts_dir / "orbital_features.parquet",
        "CREATE TABLE orbital_features(h3_cell BIGINT, hour_utc TIMESTAMP, "
        "sats_visible INTEGER, max_elevation_deg DOUBLE)",
    )
    _register_or_empty(
        con,
        "weather_features",
        marts_dir / "weather_features.parquet",
        "CREATE TABLE weather_features(h3_cell BIGINT, hour_utc TIMESTAMP, "
        "precip_mm_h DOUBLE, precip_lag_1h DOUBLE, precip_forecast_3h DOUBLE)",
    )
    _register_or_empty(
        con,
        "ookla_context",
        marts_dir / "ookla_context.parquet",
        "CREATE TABLE ookla_context(h3_cell BIGINT, tests INTEGER, devices INTEGER, "
        "terrestrial_baseline_mbps DOUBLE, terrestrial_latency_ms DOUBLE)",
    )

    return con.execute("SELECT count(*) FROM labels").fetchone()[0]  # type: ignore[index]


def run_train_models(
    con: duckdb.DuckDBPyConnection,
    marts_dir: Path,
    models_dir: Path,
    evals_dir: Path,
) -> dict:
    """Assemble inputs, build the matrix, and run the promotion-gated training.

    Returns the eval report, or ``{"skipped": ...}`` when there are no labels or too
    little history to form a time-based train/test split.
    """
    n_labels = assemble_training_inputs(con, marts_dir)
    if n_labels == 0:
        return {"skipped": "no label marts yet"}

    rows = build_training_matrix(con)
    cutoff = adaptive_cutoff(rows)
    if cutoff is None:
        return {"skipped": "not enough history to split (need >= 2 distinct hours)"}
    return run_training(rows, cutoff, models_dir, evals_dir)
