"""Postgres serving-store access (CLAUDE.md §7.2).

First DB layer in the project — the forecast/weather paths stayed in-process
through Phase 3. Raw psycopg3 over a small connection pool, no ORM: the schema is
two tables of plain SQL and an ORM would be exactly the machinery §11 rejects.

The pool is a process singleton opened lazily from ``Settings.database_url``. Tests
point it at an ephemeral testcontainers Postgres via ``configure_pool``.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import LiteralString, cast

from psycopg import Connection
from psycopg_pool import ConnectionPool

from .config import get_settings

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_pool: ConnectionPool | None = None


def init_schema(conn: Connection) -> None:
    """Apply the idempotent DDL (§7.2). Safe to run on every startup."""
    # The schema is a trusted static file, not user input, so casting away the
    # LiteralString requirement is safe here (psycopg accepts a runtime str query).
    conn.execute(cast(LiteralString, SCHEMA_PATH.read_text()))
    conn.commit()


def configure_pool(conninfo: str) -> ConnectionPool:
    """Replace the process pool (used at startup and by the test harness)."""
    global _pool
    if _pool is not None:
        _pool.close()
    _pool = ConnectionPool(conninfo, min_size=1, max_size=10, open=True)
    return _pool


def get_pool() -> ConnectionPool:
    """The process pool, opened lazily from settings on first use."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(get_settings().database_url, min_size=1, max_size=10, open=True)
    return _pool


def get_conn() -> Iterator[Connection]:
    """FastAPI dependency yielding a pooled connection.

    psycopg's context manager commits on clean exit and rolls back on exception,
    so a handler that raises never half-writes a batch.
    """
    with get_pool().connection() as conn:
        yield conn
