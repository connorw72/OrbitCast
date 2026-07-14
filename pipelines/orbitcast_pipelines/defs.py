"""Dagster definitions (CLAUDE.md D4, §5.5).

One container (webserver + daemon) orchestrates the six small jobs. Software-
defined assets wrap the ingestion functions; schedules give the cadences from
§5.5. Dagster was chosen over Airflow deliberately (§5.5) — same concepts (DAGs,
schedules, sensors, retries, backfills) at a fraction of the memory footprint.
"""

import json
import time

from dagster import (
    AssetExecutionContext,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)
from orbitcast_core.celestrak import fetch_with_cache, latest_cache
from orbitcast_core.orbital import satellites_from_gp

from . import atlas, ookla, warehouse
from .orbital_mart import build_orbital_features, label_cell_hours
from .registry import active_cells
from .training import run_train_models
from .validate import assert_mart
from .weather_mart import build_weather_mart_from_marts

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
    # Merge into the existing mart: the fallback stats and the rolling-median
    # training feature need a trailing window, not just the last 24 h.
    mart_path = warehouse.marts_dir() / "atlas_latency_hourly.parquet"
    existing = warehouse.read_mart(mart_path) if mart_path.exists() else []
    merged = atlas.merge_hourly(existing, rows)
    warehouse.write_mart(merged, mart_path)
    context.log.info(
        f"atlas_latency: {len(rows)} new rows from {len(cells)} probes; mart now {len(merged)}"
    )


@asset(description="Crowdsourced measurements (Postgres) -> hourly user labels (§4.3, §6.2).")
def user_measurements(context: AssetExecutionContext) -> None:
    from .user_measurements import build_user_measurements_mart

    try:
        mart = build_user_measurements_mart(warehouse.marts_dir())
    except Exception:
        # Training must not break when the serving DB is momentarily unreachable
        # (F10) — degrade to the other label sources, like mlab_labels does.
        context.log.warning("user_measurements skipped: serving store unreachable", exc_info=True)
        return
    context.log.info(f"user_measurements rows: {len(mart)}")


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


@asset(
    deps=[atlas_latency],
    description="Serving fallback marts: cell_label_stats + latitude_priors (§6.3).",
)
def fallback_stats(context: AssetExecutionContext) -> None:
    from .fallback_marts import build_fallback_marts

    con = warehouse.connect()
    n_stats, n_priors = build_fallback_marts(con, warehouse.marts_dir())
    if n_stats == 0:
        context.log.warning("fallback_stats: no label marts yet, nothing written")
    else:
        context.log.info(f"fallback_stats: {n_stats} cell stats, {n_priors} latitude priors")


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
    deps=[atlas_latency, user_measurements],
    description="Hourly orbital supply features at label (cell,hour) pairs (§4.1, §5.5).",
)
def orbital_features(context: AssetExecutionContext) -> None:
    marts = warehouse.marts_dir()
    cell_hours = label_cell_hours(marts)
    latest = latest_cache(warehouse.DATA_DIR / "raw" / "celestrak")
    sats = satellites_from_gp(json.loads(latest.read_text())) if latest else []
    rows = build_orbital_features(sats, cell_hours)
    warehouse.write_mart(rows, marts / "orbital_features.parquet")
    context.log.info(f"orbital_features rows: {len(rows)} from {len(sats)} satellites")


@asset(
    deps=[atlas_latency, user_measurements],
    description="ERA5 historical precipitation features at label (cell,hour) pairs (§4.4).",
)
def weather_history(context: AssetExecutionContext) -> None:
    marts = warehouse.marts_dir()
    rows = build_weather_mart_from_marts(marts)
    warehouse.write_mart(rows, marts / "weather_features.parquet")
    context.log.info(f"weather_features rows: {len(rows)}")


@asset(
    deps=[atlas_latency, user_measurements, ookla_context, orbital_features, weather_history],
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
atlas_job = define_asset_job(
    "atlas_ingest", selection=[atlas_latency, active_cell_registry, fallback_stats]
)
mlab_job = define_asset_job("mlab_ingest", selection=[mlab_labels])
ookla_job = define_asset_job("ookla_ingest", selection=[ookla_context])
train_job = define_asset_job(
    "train_models",
    selection=[user_measurements, orbital_features, weather_history, train_models],
)

defs = Definitions(
    assets=[
        celestrak_gp,
        ookla_context,
        atlas_latency,
        user_measurements,
        active_cell_registry,
        fallback_stats,
        mlab_labels,
        weather_forecast,
        orbital_features,
        weather_history,
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
