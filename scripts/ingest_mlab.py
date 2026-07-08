"""Ingest one month of M-Lab Starlink throughput+latency, then retrain.

Pulls NDT7 rows for Starlink (ASN 14593) from BigQuery, folds them into res-4
(cell, hour) labels (CLAUDE.md §4.2a, F2), writes the mart, and runs the
promotion-gated training — which now trains the download-throughput booster
because `dl_throughput` labels exist.

Prerequisite: BigQuery Application Default Credentials + the M-Lab group (see
docs/mlab-setup.md), and `uv add --package orbitcast-pipelines google-cloud-bigquery`.

A full month scans ~1.6 TB, over BigQuery's free 1 TB/month quota. Pass an
optional inclusive day-range to stay under the free tier (days 1-14 ~= 0.8 TB):

Usage:
    uv run --package orbitcast-pipelines python scripts/ingest_mlab.py 2026 6
    uv run --package orbitcast-pipelines python scripts/ingest_mlab.py 2026 6 1 14
"""

import sys

from orbitcast_pipelines import mlab, warehouse
from orbitcast_pipelines.training import run_train_models


def main() -> None:
    if len(sys.argv) not in (3, 5):
        print("usage: ingest_mlab.py <year> <month> [start_day end_day]", file=sys.stderr)
        raise SystemExit(2)
    year, month = int(sys.argv[1]), int(sys.argv[2])
    start_day = int(sys.argv[3]) if len(sys.argv) == 5 else 1
    stop_day = int(sys.argv[4]) if len(sys.argv) == 5 else None

    # Imported here so the module (and CI lint) doesn't require the BigQuery SDK,
    # which is only installed once M-Lab access is set up.
    from google.cloud import bigquery  # type: ignore[import-not-found]

    client = bigquery.Client()
    span = f"{start_day:02d}..{stop_day:02d}" if stop_day else "full month"
    print(
        f"mlab: querying NDT7 for Starlink AS{mlab.STARLINK_ASN}, "
        f"{year}-{month:02d} ({span})..."
    )
    raw = mlab.ingest_mlab_month(client, year, month, start_day=start_day, stop_day=stop_day)
    rows = mlab.aggregate_mlab_to_labels(raw)
    marts = warehouse.marts_dir()
    warehouse.write_mart(rows, marts / "mlab_throughput_hourly.parquet")
    print(f"mlab: {len(raw)} raw rows -> {len(rows)} res-4 (cell, hour) labels")

    con = warehouse.connect()
    report = run_train_models(
        con, marts, warehouse.DATA_DIR / "models", warehouse.DATA_DIR / "evals"
    )
    if report.get("skipped"):
        print(f"train: SKIPPED ({report['skipped']})")
        return
    print(f"train: version={report['version']} promoted={report['promoted']}")
    for target, m in report["targets"].items():
        print(
            f"  {target}: coverage={m['coverage']:.2f} q50_mae={m['q50_mae']:.2f} "
            f"persistence_mae={m['persistence_mae']:.2f} promote={m['promote']}"
        )


if __name__ == "__main__":
    main()
