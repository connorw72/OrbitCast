"""One-command data-spine backfill (CLAUDE.md Phase 2 DoD).

Backfills the current Ookla quarter + recent RIPE Atlas latency into data/marts/,
building the active-cell registry, and asserts row-count / null-rate bounds on
every mart. M-Lab is deferred (needs BigQuery creds).

    uv run python scripts/backfill.py                 # bounded, fast verify
    uv run python scripts/backfill.py --ookla-limit 0 # full quarter (slow)
"""

import argparse
import time

from orbitcast_pipelines import atlas, ookla, warehouse
from orbitcast_pipelines.registry import active_cells
from orbitcast_pipelines.validate import assert_mart


def run(
    ookla_year: int,
    ookla_quarter: int,
    ookla_limit: int | None,
    atlas_days: int,
    atlas_max_probes: int,
) -> None:
    marts = warehouse.marts_dir()
    con = warehouse.connect()

    # --- Ookla context (public S3, no creds) ---
    url = ookla.quarter_url(ookla_year, ookla_quarter)
    source = (
        f"read_parquet('{url}')"
        if not ookla_limit
        else f"(SELECT * FROM read_parquet('{url}') LIMIT {ookla_limit})"
    )
    ookla_rows = ookla.aggregate_ookla_to_h3(con, source)
    assert_mart(ookla_rows, ["h3_cell", "tests", "terrestrial_baseline_mbps"], min_rows=1)
    warehouse.write_mart(ookla_rows, marts / "ookla_context.parquet")
    print(f"ookla_context.parquet: {len(ookla_rows)} cells")

    # --- RIPE Atlas latency (public, no credits) ---
    probes = atlas.enumerate_probes()
    cells = atlas.probes_to_cells(probes)
    stop = int(time.time())
    start = stop - atlas_days * 86400
    results: list[dict] = []
    for pid in list(cells)[:atlas_max_probes]:
        msm = atlas.find_ping_measurement(pid)
        if msm:
            results.extend(atlas.fetch_ping_results(msm, start, stop, probe_ids=[pid]))
    atlas_rows = atlas.aggregate_pings_to_hourly(results, cells)
    assert_mart(atlas_rows, ["h3_cell", "hour_utc", "rtt_ms_median"], min_rows=1)
    warehouse.write_mart(atlas_rows, marts / "atlas_latency_hourly.parquet")
    print(
        f"atlas_latency_hourly.parquet: {len(atlas_rows)} rows "
        f"({len(cells)} probes on AS{atlas.STARLINK_ASN})"
    )

    # --- active-cell registry ---
    registry = [{"h3_cell": c} for c in active_cells(ookla_rows, atlas_rows)]
    warehouse.write_mart(registry, marts / "active_cells.parquet")
    print(f"active_cells.parquet: {len(registry)} cells")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ookla-year", type=int, default=2025)
    p.add_argument("--ookla-quarter", type=int, default=1)
    p.add_argument(
        "--ookla-limit",
        type=int,
        default=200_000,
        help="row cap for a fast verify; 0 = full quarter",
    )
    p.add_argument("--atlas-days", type=int, default=3)
    p.add_argument("--atlas-max-probes", type=int, default=20)
    a = p.parse_args()
    run(a.ookla_year, a.ookla_quarter, a.ookla_limit or None, a.atlas_days, a.atlas_max_probes)
