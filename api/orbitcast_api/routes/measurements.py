"""POST /v1/measurements — authenticated batch ingest (CLAUDE.md §7.3, §4.3).

Accepts a validated batch of crowdsourced samples from the dish reporter or the
browser probe and appends them to the serving store, linked to the authenticated
user. Rate-limited per user (i.e. per token) to bound ingest floods.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection

from ..auth import require_user
from ..db import get_conn
from ..deps import get_measurement_rate_limiter
from ..ratelimit import RateLimiter
from ..schemas import MeasurementBatch, MeasurementBatchResult

router = APIRouter()


@router.post("/v1/measurements")
def ingest_measurements(
    body: MeasurementBatch,
    user_id: Annotated[str, Depends(require_user)],
    conn: Annotated[Connection, Depends(get_conn)],
    limiter: Annotated[RateLimiter, Depends(get_measurement_rate_limiter)],
) -> MeasurementBatchResult:
    if not limiter.allow(user_id):
        raise HTTPException(status_code=429, detail="Too many requests")

    rows = [
        (
            user_id,
            m.ts,
            m.h3_cell,
            m.source,
            m.latency_ms,
            m.dl_mbps,
            m.ul_mbps,
            m.obstruction_pct,
            m.hw_version,
        )
        for m in body.measurements
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO measurements "
            "(user_id, ts, h3_cell, source, latency_ms, dl_mbps, ul_mbps, "
            " obstruction_pct, hw_version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
    return MeasurementBatchResult(accepted=len(rows))
