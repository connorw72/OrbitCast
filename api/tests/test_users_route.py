"""POST /v1/users — mint an anonymous token (CLAUDE.md §7.3, D12).

No email, no signup. The response carries the raw token exactly once; the server
persists only its SHA-256 hash and the user's declared res-5 cell.
"""

from orbitcast_api import db, deps
from orbitcast_api.ratelimit import RateLimiter
from orbitcast_api.security import hash_token


def test_create_user_returns_token_and_persists_only_the_hash(client) -> None:
    resp = client.post("/v1/users", json={"h3_cell": 599686042433355775})
    assert resp.status_code == 200
    body = resp.json()
    token = body["token"]
    assert token and isinstance(token, str)
    assert "user_id" in body

    # The raw token is never stored; only its hash, keyed to the new user.
    with db.get_pool().connection() as conn:
        row = conn.execute(
            "SELECT token_hash, h3_cell FROM users WHERE id = %s",
            (body["user_id"],),
        ).fetchone()
    assert row is not None
    assert row[0] == hash_token(token)
    assert row[0] != token
    assert row[1] == 599686042433355775


def test_create_user_without_cell_is_allowed(client) -> None:
    resp = client.post("/v1/users", json={})
    assert resp.status_code == 200
    with db.get_pool().connection() as conn:
        row = conn.execute(
            "SELECT h3_cell FROM users WHERE id = %s", (resp.json()["user_id"],)
        ).fetchone()
    assert row is not None
    assert row[0] is None


def test_tokens_are_distinct_across_users(client) -> None:
    a = client.post("/v1/users", json={}).json()["token"]
    b = client.post("/v1/users", json={}).json()["token"]
    assert a != b


def test_create_user_is_rate_limited(client, app_with_limits) -> None:
    limiter = RateLimiter(max_requests=2, window_seconds=3600)
    app_with_limits.dependency_overrides[deps.get_user_rate_limiter] = lambda: limiter

    assert client.post("/v1/users", json={}).status_code == 200
    assert client.post("/v1/users", json={}).status_code == 200
    # Third mint from the same client within the window is refused.
    assert client.post("/v1/users", json={}).status_code == 429


def test_invalid_cell_is_rejected(client) -> None:
    # H3 ids are BIGINT; a non-integer must not reach the DB.
    resp = client.post("/v1/users", json={"h3_cell": "not-a-number"})
    assert resp.status_code == 422
    with db.get_pool().connection() as conn:
        (count,) = conn.execute("SELECT count(*) FROM users").fetchone()  # type: ignore[misc]
    assert count == 0
