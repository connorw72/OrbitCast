"""train_models input assembly (CLAUDE.md §5.5, §6).

The weekly training job fuses the label marts into the long-form `labels` table the
training-matrix builder expects, registering (possibly empty) orbital/weather/ookla
inputs so the fusion SQL runs even before those ingests exist.
"""

from datetime import UTC, datetime

import h3
from orbitcast_ml.training_matrix import build_training_matrix
from orbitcast_pipelines import warehouse
from orbitcast_pipelines.training import assemble_training_inputs

_CELL = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 5))


def test_atlas_mart_becomes_latency_labels(tmp_path):
    marts = tmp_path / "marts"
    marts.mkdir()
    warehouse.write_mart(
        [
            {
                "h3_cell": _CELL,
                "hour_utc": datetime(2026, 7, 6, 12, tzinfo=UTC),
                "rtt_ms_median": 42.0,
                "samples": 5,
            },
            {
                "h3_cell": _CELL,
                "hour_utc": datetime(2026, 7, 6, 13, tzinfo=UTC),
                "rtt_ms_median": 48.0,
                "samples": 6,
            },
        ],
        marts / "atlas_latency_hourly.parquet",
    )

    con = warehouse.connect(tmp_path / "w.duckdb")
    n = assemble_training_inputs(con, marts)
    assert n == 2

    rows = build_training_matrix(con)
    assert len(rows) == 2
    assert all(r["target"] == "latency" for r in rows)
    labels = sorted(r["label"] for r in rows)
    assert labels == [42.0, 48.0]


def test_no_marts_yields_zero_labels(tmp_path):
    con = warehouse.connect(tmp_path / "w.duckdb")
    n = assemble_training_inputs(con, tmp_path / "marts")
    assert n == 0
    assert build_training_matrix(con) == []
