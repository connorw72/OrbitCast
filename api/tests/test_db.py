"""Serving-store schema (CLAUDE.md §7.2): the DDL applies idempotently and creates
the users + measurements tables with the expected shape."""

import psycopg
from orbitcast_api import db


def test_init_schema_creates_tables(pg_dsn: str, _schema: None) -> None:
    pool = db.configure_pool(pg_dsn)
    try:
        with pool.connection() as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
        tables = {r[0] for r in rows}
        assert {"users", "measurements"} <= tables
    finally:
        pool.close()
        db._pool = None


def test_init_schema_is_idempotent(pg_dsn: str, _schema: None) -> None:
    # Applying the DDL a second time must not raise (CREATE ... IF NOT EXISTS).
    with psycopg.connect(pg_dsn) as conn:
        db.init_schema(conn)
        db.init_schema(conn)


def test_app_startup_self_migrates_a_fresh_database(pg_dsn: str) -> None:
    """First-boot path: on a fresh Postgres volume the app's lifespan creates the
    schema, so `docker compose up` needs no manual migration step (§7.2)."""
    from fastapi.testclient import TestClient

    # Simulate a fresh volume: no tables present.
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("DROP TABLE IF EXISTS measurements, users CASCADE")
        conn.commit()

    db.configure_pool(pg_dsn)
    try:
        from orbitcast_api.main import app

        with TestClient(app):  # entering the context runs the startup lifespan
            pass
        with db.get_pool().connection() as conn:
            regs = conn.execute(
                "SELECT to_regclass('public.users'), to_regclass('public.measurements')"
            ).fetchone()
        assert regs is not None
        assert regs[0] is not None and regs[1] is not None
    finally:
        db.get_pool().close()
        db._pool = None


def test_users_token_hash_is_unique(db_pool: None) -> None:
    import psycopg
    from orbitcast_api import db as dbmod

    pool = dbmod.get_pool()
    with pool.connection() as conn:
        conn.execute("INSERT INTO users (token_hash) VALUES ('dup')")
        conn.commit()
        try:
            with conn.transaction():
                conn.execute("INSERT INTO users (token_hash) VALUES ('dup')")
            raise AssertionError("expected a unique-violation on duplicate token_hash")
        except psycopg.errors.UniqueViolation:
            pass
