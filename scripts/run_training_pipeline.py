"""One-shot real-data run: CelesTrak -> RIPE Atlas -> orbital/weather marts -> train.

Mirrors the Dagster assets (defs.py) but runs inline with progress prints so we can
watch a first real training happen. Hits live networks (CelesTrak, RIPE Atlas,
Open-Meteo ERA5) — all read-only public data.
"""

import time
from datetime import UTC, datetime

from orbitcast_core.celestrak import fetch_with_cache, latest_cache
from orbitcast_core.orbital import satellites_from_gp
from orbitcast_pipelines import atlas, warehouse
from orbitcast_pipelines.orbital_mart import build_orbital_features, label_cell_hours
from orbitcast_pipelines.training import run_train_models
from orbitcast_pipelines.weather_mart import build_weather_mart_from_marts

DATA = warehouse.DATA_DIR
MARTS = warehouse.marts_dir()


def log(msg: str) -> None:
    print(f"[{datetime.now(UTC):%H:%M:%S}] {msg}", flush=True)


def main() -> None:
    # 1. CelesTrak GP cache (satellite positions).
    log("celestrak: fetching GP...")
    records = fetch_with_cache(DATA / "raw" / "celestrak")
    log(f"celestrak: {len(records)} GP objects cached")

    # 2. RIPE Atlas Starlink latency -> hourly mart.
    log("atlas: enumerating probes on AS14593...")
    cells = atlas.probes_to_cells(atlas.enumerate_probes())
    log(f"atlas: {len(cells)} probes with coordinates")
    stop = int(time.time())
    start = stop - 86400  # last 24h
    results: list[dict] = []
    for i, pid in enumerate(cells, 1):
        msm = atlas.find_ping_measurement(pid)
        if msm:
            results.extend(atlas.fetch_ping_results(msm, start, stop, probe_ids=[pid]))
        if i % 20 == 0:
            log(f"atlas: {i}/{len(cells)} probes, {len(results)} ping results so far")
    rows = atlas.aggregate_pings_to_hourly(results, cells)
    warehouse.write_mart(rows, MARTS / "atlas_latency_hourly.parquet")
    log(f"atlas: {len(rows)} (cell,hour) latency rows")

    cell_hours = label_cell_hours(MARTS)
    log(f"labels: {len(cell_hours)} distinct (cell,hour) pairs to feature-ize")
    if not cell_hours:
        log("STOP: no atlas labels — cannot train yet.")
        return

    # 3. Orbital features at each label (cell,hour).
    log("orbital: propagating satellites at label hours...")
    latest = latest_cache(DATA / "raw" / "celestrak")
    sats = satellites_from_gp(records) if latest else []
    orb = build_orbital_features(sats, cell_hours)
    warehouse.write_mart(orb, MARTS / "orbital_features.parquet")
    log(f"orbital: {len(orb)} rows from {len(sats)} satellites")

    # 4. Weather features (ERA5 archive) at each label (cell,hour).
    log("weather: fetching ERA5 precip per cell...")
    wx = build_weather_mart_from_marts(MARTS)
    warehouse.write_mart(wx, MARTS / "weather_features.parquet")
    log(f"weather: {len(wx)} rows")

    # 5. Train + promotion gate.
    log("train: building matrix + training latency booster...")
    con = warehouse.connect()
    report = run_train_models(con, MARTS, DATA / "models", DATA / "evals")
    if report.get("skipped"):
        log(f"train: SKIPPED ({report['skipped']})")
        return
    log(f"train: version={report['version']} promoted={report['promoted']}")
    for target, m in report["targets"].items():
        log(
            f"  {target}: coverage={m['coverage']:.2f} "
            f"q50_mae={m['q50_mae']:.2f} persistence_mae={m['persistence_mae']:.2f} "
            f"promote={m['promote']}"
        )
    log("DONE.")


if __name__ == "__main__":
    main()
