"""Shared test harness for the DB-backed API (CLAUDE.md §10 — integration tests
against dockerized Postgres).

A single ephemeral ``postgres:16`` container is started for the whole test session
(via testcontainers), the schema is applied once, and every test runs against it
with the tables truncated between tests for isolation. Requires a running Docker
(OrbStack locally, the Docker service in CI); these tests are skipped if the
container cannot start.
"""

import os
from collections.abc import Iterator

import psycopg
import pytest
from orbitcast_api import db


def _ensure_docker_host() -> None:
    """Point docker-py at a working socket when the default one is unusable.

    CI and Docker Desktop expose /var/run/docker.sock, which testcontainers finds
    on its own. OrbStack relocates the socket and leaves the default symlink
    dangling, so we fall back to its real path — only if DOCKER_HOST isn't already
    set and the default socket doesn't resolve.
    """
    if os.environ.get("DOCKER_HOST"):
        return
    default = "/var/run/docker.sock"
    if os.path.exists(default) and os.path.exists(os.path.realpath(default)):
        return
    orbstack = os.path.expanduser("~/.orbstack/run/docker.sock")
    if os.path.exists(orbstack):
        os.environ["DOCKER_HOST"] = f"unix://{orbstack}"


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    _ensure_docker_host()
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:  # pragma: no cover - dev dependency guard
        pytest.skip("testcontainers not installed")

    try:
        container = PostgresContainer("postgres:16", driver=None)
        container.start()
    except Exception as exc:  # pragma: no cover - no Docker available
        pytest.skip(f"Docker unavailable for integration tests: {exc}")

    dsn = container.get_connection_url()  # postgresql://user:pass@host:port/db
    try:
        yield dsn
    finally:
        container.stop()


@pytest.fixture(scope="session")
def _schema(pg_dsn: str) -> None:
    with psycopg.connect(pg_dsn) as conn:
        db.init_schema(conn)


@pytest.fixture
def db_pool(pg_dsn: str, _schema: None) -> Iterator[None]:
    """Point the process pool at the container and truncate between tests."""
    pool = db.configure_pool(pg_dsn)
    with pool.connection() as conn:
        conn.execute("TRUNCATE measurements, users, forecast_cache RESTART IDENTITY CASCADE")
        conn.commit()
    yield
    pool.close()
    db._pool = None


@pytest.fixture
def app_with_limits(db_pool: None):
    """The app with generous per-test rate limits (so the process-global limiters
    can't leak counts across tests). Limit-specific tests override again."""
    from orbitcast_api import deps
    from orbitcast_api.main import app
    from orbitcast_api.ratelimit import RateLimiter

    generous_users = RateLimiter(max_requests=10_000, window_seconds=3600)
    generous_measurements = RateLimiter(max_requests=10_000, window_seconds=60)
    app.dependency_overrides[deps.get_user_rate_limiter] = lambda: generous_users
    app.dependency_overrides[deps.get_measurement_rate_limiter] = lambda: generous_measurements
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(app_with_limits) -> Iterator["object"]:
    from fastapi.testclient import TestClient

    with TestClient(app_with_limits) as c:
        yield c
