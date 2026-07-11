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


def test_mlab_mart_becomes_throughput_and_latency_labels(tmp_path):
    marts = tmp_path / "marts"
    marts.mkdir()
    cell4 = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 4))
    warehouse.write_mart(
        [
            {
                "h3_cell": cell4,
                "hour_utc": datetime(2026, 7, 6, 12, tzinfo=UTC),
                "dl_mbps_median": 150.0,
                "rtt_ms_median": 38.0,
                "samples": 20,
            }
        ],
        marts / "mlab_throughput_hourly.parquet",
    )

    con = warehouse.connect(tmp_path / "w.duckdb")
    n = assemble_training_inputs(con, marts)
    assert n == 2  # one throughput label + one latency label from the same row

    rows = build_training_matrix(con)
    by_target = {r["target"]: r for r in rows}
    assert by_target["dl_throughput"]["label"] == 150.0
    assert by_target["latency"]["label"] == 38.0
    assert by_target["dl_throughput"]["source_quality"] == 1.0  # mlab tier


def test_user_mart_becomes_user_source_labels(tmp_path):
    """Crowdsourced readings enter as long-form labels at the 'user' quality tier
    (weight 4.0), for both latency and throughput targets."""
    marts = tmp_path / "marts"
    marts.mkdir()
    warehouse.write_mart(
        [
            {
                "h3_cell": _CELL,
                "hour_utc": datetime(2026, 7, 6, 20, tzinfo=UTC),
                "target": "latency",
                "value_median": 55.0,
                "samples": 30,
            },
            {
                "h3_cell": _CELL,
                "hour_utc": datetime(2026, 7, 6, 20, tzinfo=UTC),
                "target": "dl_throughput",
                "value_median": 90.0,
                "samples": 12,
            },
        ],
        marts / "user_measurements_hourly.parquet",
    )

    con = warehouse.connect(tmp_path / "w.duckdb")
    assert assemble_training_inputs(con, marts) == 2

    by_target = {r["target"]: r for r in build_training_matrix(con)}
    assert by_target["latency"]["label"] == 55.0
    assert by_target["dl_throughput"]["label"] == 90.0
    assert by_target["latency"]["source_quality"] == 4.0  # user tier
    assert by_target["dl_throughput"]["source_quality"] == 4.0


def test_no_marts_yields_zero_labels(tmp_path):
    con = warehouse.connect(tmp_path / "w.duckdb")
    n = assemble_training_inputs(con, tmp_path / "marts")
    assert n == 0
    assert build_training_matrix(con) == []


def test_end_to_end_latency_only_training(tmp_path):
    """With only Atlas (latency) labels + the orbital/weather marts, train_models
    trains the latency booster alone without erroring (M-Lab throughput deferred)."""
    from datetime import timedelta

    from orbitcast_pipelines.orbital_mart import build_orbital_features, label_cell_hours
    from orbitcast_pipelines.training import run_train_models
    from orbitcast_pipelines.weather_mart import build_weather_features

    marts = tmp_path / "marts"
    marts.mkdir()
    now = datetime(2026, 7, 5, tzinfo=UTC)
    base = now - timedelta(days=60)
    atlas_rows = [
        {
            "h3_cell": _CELL,
            "hour_utc": base + timedelta(days=d, hours=12),
            "rtt_ms_median": 40.0 + (d % 5),
            "samples": 4,
        }
        for d in range(60)
    ]
    warehouse.write_mart(atlas_rows, marts / "atlas_latency_hourly.parquet")

    cell_hours = label_cell_hours(marts)
    warehouse.write_mart(build_orbital_features([], cell_hours), marts / "orbital_features.parquet")
    warehouse.write_mart(build_weather_features(cell_hours, {}), marts / "weather_features.parquet")

    con = warehouse.connect(tmp_path / "w.duckdb")
    report = run_train_models(con, marts, tmp_path / "models", tmp_path / "evals")

    assert "skipped" not in report
    assert set(report["targets"]) == {"latency"}
    assert isinstance(report["promoted"], bool)
