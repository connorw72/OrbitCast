"""Fallback-mart builder: cell_label_stats + latitude_priors (CLAUDE.md §6.3).

These are the serving-side inputs to `resolve_cell_median`: without them every
forecast answers as ``latitude_prior`` with a NaN median, which also disables the
takeaways engine's congestion detection. Built from the latency label marts
(Atlas res-5, M-Lab res-4, user measurements res-5 — M-Lab never enters at res-5,
F2), with res-4/res-3 parent roll-ups so the resolver can walk up the hierarchy.

Window semantics: the median covers each cell's *own* most recent 7 labeled days
(``max(hour_utc) OVER (PARTITION BY h3_cell)`` anchored), mirroring the training
feature's trailing 7-day window — a per-source ingest gap (M-Lab is monthly)
shifts a cell's window back rather than zeroing out its baseline. ``hours`` is
the distinct labeled hours inside that window; serving gates cell/region basis
at >= 168 of them. The latitude priors use *all* history — a prior of last
resort should be stable, not seasonal.
"""

from pathlib import Path

import duckdb
from orbitcast_ml.fallback import BAND_WIDTH_DEG

from . import warehouse

_STATS_MART = "cell_label_stats.parquet"
_PRIORS_MART = "latitude_priors.parquet"

# (mart filename, SELECT of h3_cell/hour_utc/value for its latency labels)
_LATENCY_SOURCES = (
    ("atlas_latency_hourly.parquet", "SELECT h3_cell, hour_utc, rtt_ms_median FROM {src}"),
    (
        "mlab_throughput_hourly.parquet",
        "SELECT h3_cell, hour_utc, rtt_ms_median FROM {src} WHERE rtt_ms_median IS NOT NULL",
    ),
    (
        "user_measurements_hourly.parquet",
        "SELECT h3_cell, hour_utc, value_median FROM {src} WHERE target = 'latency'",
    ),
)


def latency_labels_sql(marts_dir: Path) -> str | None:
    """UNION ALL over whichever latency label marts exist; None when none do."""
    parts = []
    for name, select in _LATENCY_SOURCES:
        path = marts_dir / name
        if path.exists():
            body = select.format(src=f"read_parquet('{path}')")
            parts.append(
                f"SELECT h3_cell::BIGINT AS h3_cell, hour_utc::TIMESTAMPTZ AS hour_utc,"
                f" value::DOUBLE AS value FROM ({body}) t(h3_cell, hour_utc, value)"
            )
    return " UNION ALL ".join(parts) if parts else None


def build_fallback_marts(con: duckdb.DuckDBPyConnection, marts_dir: Path) -> tuple[int, int]:
    """Write both marts from the label marts; returns (stats rows, prior rows).

    With no label mart present nothing is written — serving already degrades to
    the NaN-median latitude_prior path on missing marts.
    """
    marts_dir = Path(marts_dir)
    labels = latency_labels_sql(marts_dir)
    if labels is None:
        return 0, 0

    stats = con.execute(
        f"""
        WITH labels AS ({labels}),
        levels AS (
            SELECT h3_cell, hour_utc, value FROM labels
            UNION ALL
            SELECT h3_cell_to_parent(h3_cell::UBIGINT, 4)::BIGINT, hour_utc, value
            FROM labels WHERE h3_get_resolution(h3_cell::UBIGINT) > 4
            UNION ALL
            SELECT h3_cell_to_parent(h3_cell::UBIGINT, 3)::BIGINT, hour_utc, value
            FROM labels WHERE h3_get_resolution(h3_cell::UBIGINT) > 3
        ),
        anchored AS (
            SELECT *, max(hour_utc) OVER (PARTITION BY h3_cell) AS cell_last
            FROM levels
        )
        SELECT h3_cell,
               quantile_cont(value, 0.5) AS median,
               count(DISTINCT hour_utc)::INT AS hours
        FROM anchored
        WHERE hour_utc > cell_last - INTERVAL 7 DAY
        GROUP BY h3_cell
        ORDER BY h3_cell
        """
    ).fetchall()

    priors = con.execute(
        f"""
        WITH labels AS ({labels})
        SELECT floor(h3_cell_to_lat(h3_cell::UBIGINT) / {BAND_WIDTH_DEG})::INT AS band,
               quantile_cont(value, 0.5) AS median
        FROM labels
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()

    warehouse.write_mart(
        [{"h3_cell": int(c), "median": float(m), "hours": int(h)} for c, m, h in stats],
        marts_dir / _STATS_MART,
    )
    warehouse.write_mart(
        [{"band": int(b), "median": float(m)} for b, m in priors],
        marts_dir / _PRIORS_MART,
    )
    return len(stats), len(priors)
