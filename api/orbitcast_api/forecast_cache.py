"""Postgres write-through for served forecasts (CLAUDE.md §7.2, design spec Part 1b).

`select_bands` / `upsert_bands` are the conn-level primitives (integration-tested
against dockerized Postgres). `read_cached` / `write_through` are the pool-level
wrappers the routes use: the cache is an optimization, so a missing or unreachable
Postgres degrades to compute-every-time serving (the pre-Phase-4 posture) instead
of failing the request — with a warning, not silently.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from psycopg import Connection

from .db import get_pool

log = logging.getLogger(__name__)

# How long a request waits for a pooled connection before serving uncached. Kept
# short: a healthy pool hands one over in microseconds, and a down Postgres must
# not add half a minute (the psycopg_pool default) to every forecast.
_POOL_TIMEOUT_S = 2.0

_METRICS = ("latency", "dl")


def select_bands(
    conn: Connection, cell: int, hours: Sequence[datetime], model_version: str
) -> dict[datetime, dict]:
    """Cached hours for a cell under the given model version.

    Returns ``{hour: {"basis", "latency": band|None, "dl": band|None}}`` with only
    the hours that have at least one metric row. A metric with no row reconstructs
    as None — same meaning as in the computed payload (target has no labels yet).
    """
    if not hours:
        return {}
    rows = conn.execute(
        "SELECT hour_utc, metric, q10, q50, q90, basis FROM forecast_cache"
        " WHERE h3_cell = %s AND model_version = %s AND hour_utc = ANY(%s)",
        (cell, model_version, list(hours)),
    ).fetchall()
    out: dict[datetime, dict] = {}
    for hour_utc, metric, q10, q50, q90, basis in rows:
        entry = out.setdefault(
            hour_utc.astimezone(UTC), {"basis": basis, "latency": None, "dl": None}
        )
        entry[metric] = {"q10": q10, "q50": q50, "q90": q90}
    return out


def upsert_bands(conn: Connection, cell: int, model_version: str, payload: Sequence[dict]) -> None:
    """Write computed payload entries (assemble_payload shape) through to Postgres.

    One row per metric that has a band; None bands write nothing. Conflicts on
    (cell, hour, metric) overwrite — the newest computation wins.
    """
    params = []
    for entry in payload:
        hour = datetime.fromisoformat(entry["hour"])
        for metric in _METRICS:
            band = entry.get(metric)
            if band is None:
                continue
            params.append(
                (
                    cell,
                    hour,
                    metric,
                    band["q10"],
                    band["q50"],
                    band["q90"],
                    entry["basis"],
                    model_version,
                )
            )
    if not params:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO forecast_cache"
            " (h3_cell, hour_utc, metric, q10, q50, q90, basis, model_version)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (h3_cell, hour_utc, metric) DO UPDATE SET"
            " q10 = EXCLUDED.q10, q50 = EXCLUDED.q50, q90 = EXCLUDED.q90,"
            " basis = EXCLUDED.basis, model_version = EXCLUDED.model_version",
            params,
        )


def read_cached(cell: int, hours: Sequence[datetime], model_version: str) -> dict[datetime, dict]:
    try:
        with get_pool().connection(timeout=_POOL_TIMEOUT_S) as conn:
            return select_bands(conn, cell, hours, model_version)
    except Exception as exc:
        log.warning("forecast_cache read unavailable, serving uncached: %s", exc)
        return {}


def write_through(cell: int, model_version: str, payload: Sequence[dict]) -> None:
    try:
        with get_pool().connection(timeout=_POOL_TIMEOUT_S) as conn:
            upsert_bands(conn, cell, model_version, payload)
    except Exception as exc:
        log.warning("forecast_cache write skipped: %s", exc)
