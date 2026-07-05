"""Dagster definitions (CLAUDE.md D4, §5.5).

One container (webserver + daemon) orchestrates the six small jobs. Software-
defined assets wrap the ingestion functions; schedules give the cadences from
§5.5. Dagster was chosen over Airflow deliberately (§5.5) — same concepts (DAGs,
schedules, sensors, retries, backfills) at a fraction of the memory footprint.
"""

import time

from dagster import (
    AssetExecutionContext,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)
from orbitcast_core.celestrak import fetch_with_cache

from . import atlas, ookla, warehouse
from .registry import active_cells
from .training import run_train_models
from .validate import assert_mart

# Latest Ookla quarter that is published (quarterly, with a lag).
_OOKLA_YEAR, _OOKLA_QUARTER = 2025, 1


@asset(description="CelesTrak supplemental GP cache refresh (every 2h).")
def celestrak_gp(context: AssetExecutionContext) -> None:
    records = fetch_with_cache(warehouse.DATA_DIR / "raw" / "celestrak")
    context.log.info(f"CelesTrak GP objects: {len(records)}")


@asset(description="Ookla quarterly context -> H3 res-5 (terrestrial baseline, demand).")
def ookla_context(context: AssetExecutionContext) -> None:
    con = warehouse.connect()
    url = ookla.quarter_url(_OOKLA_YEAR, _OOKLA_QUARTER)
    rows = ookla.aggregate_ookla_to_h3(con, f"read_parquet('{url}')")
    assert_mart(rows, ["h3_cell", "tests", "terrestrial_baseline_mbps"], min_rows=1)
    warehouse.write_mart(rows, warehouse.marts_dir() / "ookla_context.parquet")
    context.log.info(f"ookla_context cells: {len(rows)}")


@asset(description="RIPE Atlas Starlink probe latency -> hourly per cell (daily).")
def atlas_latency(context: AssetExecutionContext) -> None:
    cells = atlas.probes_to_cells(atlas.enumerate_probes())
    stop = int(time.time())
    start = stop - 86400
    results: list[dict] = []
    for pid in cells:
        msm = atlas.find_ping_measurement(pid)
        if msm:
            results.extend(atlas.fetch_ping_results(msm, start, stop, probe_ids=[pid]))
    rows = atlas.aggregate_pings_to_hourly(results, cells)
    warehouse.write_mart(rows, warehouse.marts_dir() / "atlas_latency_hourly.parquet")
    context.log.info(f"atlas_latency rows: {len(rows)} from {len(cells)} probes")


@asset(
    deps=[ookla_context, atlas_latency],
    description="Active-cell registry — union of all label sources (§6.3).",
)
def active_cell_registry(context: AssetExecutionContext) -> None:
    marts = warehouse.marts_dir()
    ookla_rows = warehouse.read_mart(marts / "ookla_context.parquet")
    atlas_rows = warehouse.read_mart(marts / "atlas_latency_hourly.parquet")
    registry = [{"h3_cell": c} for c in active_cells(ookla_rows, atlas_rows)]
    warehouse.write_mart(registry, marts / "active_cells.parquet")
    context.log.info(f"active cells: {len(registry)}")


@asset(description="M-Lab NDT monthly extract (DEFERRED — needs BigQuery creds, F2).")
def mlab_labels(context: AssetExecutionContext) -> None:
    context.log.warning(
        "mlab_labels deferred: BigQuery credentials not configured. See docs/mlab-setup.md."
    )


@asset(description="Weather refresh: 48h forecast per active cell (migrated from serving).")
def weather_forecast(context: AssetExecutionContext) -> None:
    # Wiring present; the serving path (api.weather) covers the now-cast in Phase 1.
    # The (cell, hour) forecast_cache lands in Postgres in Phase 3 (forecast).
    context.log.info("weather_forecast: hourly cadence wired; forecast_cache lands in Phase 3.")


@asset(
    deps=[atlas_latency, ookla_context],
    description="Weekly LightGBM quantile training + promotion gate (§6.4); writes "
    "model artifacts + eval report, promotes only on eval pass.",
)
def train_models(context: AssetExecutionContext) -> None:
    con = warehouse.connect()
    report = run_train_models(
        con,
        warehouse.marts_dir(),
        warehouse.DATA_DIR / "models",
        warehouse.DATA_DIR / "evals",
    )
    if report.get("skipped"):
        context.log.warning(f"train_models skipped: {report['skipped']}")
    else:
        context.log.info(f"train_models {report['version']} promoted={report['promoted']}")


celestrak_job = define_asset_job("celestrak_refresh", selection=[celestrak_gp])
weather_job = define_asset_job("weather_refresh", selection=[weather_forecast])
atlas_job = define_asset_job("atlas_ingest", selection=[atlas_latency, active_cell_registry])
mlab_job = define_asset_job("mlab_ingest", selection=[mlab_labels])
ookla_job = define_asset_job("ookla_ingest", selection=[ookla_context])
train_job = define_asset_job("train_models", selection=[train_models])

defs = Definitions(
    assets=[
        celestrak_gp,
        ookla_context,
        atlas_latency,
        active_cell_registry,
        mlab_labels,
        weather_forecast,
        train_models,
    ],
    jobs=[celestrak_job, weather_job, atlas_job, mlab_job, ookla_job, train_job],
    schedules=[
        ScheduleDefinition(job=celestrak_job, cron_schedule="0 */2 * * *"),
        ScheduleDefinition(job=weather_job, cron_schedule="0 * * * *"),
        ScheduleDefinition(job=atlas_job, cron_schedule="0 2 * * *"),
        ScheduleDefinition(job=mlab_job, cron_schedule="0 3 1 * *"),
        ScheduleDefinition(job=ookla_job, cron_schedule="0 4 1 1,4,7,10 *"),
        # Weekly, Sunday 05:00 UTC (§5.5).
        ScheduleDefinition(job=train_job, cron_schedule="0 5 * * 0"),
    ],
)
