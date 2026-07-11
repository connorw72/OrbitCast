"""Crowdsourced measurements -> hourly user-source labels (CLAUDE.md §4.3, §6.2).

The weekly training job folds the user's own readings into the label set with the
highest source-quality weight (``SOURCE_QUALITY["user"] = 4.0``). We aggregate the
serving store's ``measurements`` table to hourly medians per (cell, target) — the
same long-form shape the ``labels`` table consumes — and write it as a Parquet mart
so training stays a pure DuckDB pass over marts (the reproducible interface, §5.4).

Both collection paths feed in: the browser probe (latency only, §4.3.2) and the
dish reporter (latency + throughput, §4.3.1). NULL columns simply produce no label
for that target.
"""

import os
import statistics
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from . import warehouse

# (label target, measurements column) — a NULL column yields no label for it.
_TARGETS = (("latency", "latency_ms"), ("dl_throughput", "dl_mbps"))

_DEFAULT_DSN = "postgresql://orbitcast:orbitcast@localhost:5432/orbitcast"


def aggregate_measurements_to_hourly(rows: Sequence[dict]) -> list[dict]:
    """Hourly median per (cell, target) from raw measurement rows.

    Each input row is ``{ts, h3_cell, latency_ms, dl_mbps}``; ``ts`` is a UTC-aware
    datetime. Returns long-form ``{h3_cell, hour_utc, target, value_median, samples}``
    sorted for determinism.
    """
    buckets: dict[tuple[int, datetime, str], list[float]] = defaultdict(list)
    for row in rows:
        hour = row["ts"].replace(minute=0, second=0, microsecond=0)
        cell = int(row["h3_cell"])
        for target, col in _TARGETS:
            value = row.get(col)
            if value is not None:
                buckets[(cell, hour, target)].append(float(value))
    return [
        {
            "h3_cell": cell,
            "hour_utc": hour,
            "target": target,
            "value_median": statistics.median(values),
            "samples": len(values),
        }
        for (cell, hour, target), values in sorted(buckets.items())
    ]


def fetch_measurements(conn) -> list[dict]:
    """Read the raw measurements a label can be built from (psycopg connection)."""
    rows = conn.execute(
        "SELECT ts, h3_cell, latency_ms, dl_mbps FROM measurements "
        "WHERE latency_ms IS NOT NULL OR dl_mbps IS NOT NULL"
    ).fetchall()
    return [{"ts": r[0], "h3_cell": r[1], "latency_ms": r[2], "dl_mbps": r[3]} for r in rows]


def build_user_measurements_mart(marts_dir: Path | str, conn=None) -> list[dict]:
    """Fetch from Postgres, aggregate, and write ``user_measurements_hourly.parquet``.

    Opens its own connection from ``DATABASE_URL`` when ``conn`` is not supplied (so
    tests can pass a live testcontainers connection).
    """
    close = False
    if conn is None:
        import psycopg

        conn = psycopg.connect(os.environ.get("DATABASE_URL", _DEFAULT_DSN))
        close = True
    try:
        raw = fetch_measurements(conn)
    finally:
        if close:
            conn.close()

    mart = aggregate_measurements_to_hourly(raw)
    warehouse.write_mart(mart, Path(marts_dir) / "user_measurements_hourly.parquet")
    return mart
