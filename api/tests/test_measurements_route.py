"""POST /v1/measurements — authenticated batch ingest (CLAUDE.md §7.3, §4.3).

Bearer-token auth against the anonymous user, validated batch, rate-limited per
token. Locations are res-5 cells chosen client-side (D12).
"""

from orbitcast_api import db, deps
from orbitcast_api.ratelimit import RateLimiter

CELL = 599686042433355775


def _new_token(client) -> str:
    return client.post("/v1/users", json={}).json()["token"]


def _sample(**over) -> dict:
    base = {
        "ts": "2026-07-07T20:00:00Z",
        "h3_cell": CELL,
        "source": "reporter",
        "latency_ms": 42.0,
        "dl_mbps": 120.0,
        "ul_mbps": 15.0,
        "obstruction_pct": 1.5,
        "hw_version": "rev3_proto2",
    }
    base.update(over)
    return base


def test_ingest_persists_batch_linked_to_user(client) -> None:
    token = _new_token(client)
    resp = client.post(
        "/v1/measurements",
        json={"measurements": [_sample(), _sample(latency_ms=50.0, source="browser")]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 2}

    with db.get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT m.h3_cell, m.source, m.latency_ms FROM measurements m "
            "JOIN users u ON u.id = m.user_id "
            "ORDER BY m.id"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == CELL
    assert {r[1] for r in rows} == {"reporter", "browser"}


def test_ingest_requires_a_bearer_token(client) -> None:
    resp = client.post("/v1/measurements", json={"measurements": [_sample()]})
    assert resp.status_code == 401


def test_ingest_rejects_an_unknown_token(client) -> None:
    resp = client.post(
        "/v1/measurements",
        json={"measurements": [_sample()]},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


def test_ingest_rejects_a_malformed_auth_header(client) -> None:
    token = _new_token(client)
    resp = client.post(
        "/v1/measurements",
        json={"measurements": [_sample()]},
        headers={"Authorization": token},  # missing "Bearer " scheme
    )
    assert resp.status_code == 401


def test_ingest_validates_source(client) -> None:
    token = _new_token(client)
    resp = client.post(
        "/v1/measurements",
        json={"measurements": [_sample(source="satellite")]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_ingest_rejects_empty_batch(client) -> None:
    token = _new_token(client)
    resp = client.post(
        "/v1/measurements",
        json={"measurements": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_ingest_is_rate_limited_per_token(client, app_with_limits) -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    app_with_limits.dependency_overrides[deps.get_measurement_rate_limiter] = lambda: limiter
    token = _new_token(client)
    hdr = {"Authorization": f"Bearer {token}"}
    batch = {"measurements": [_sample()]}
    assert client.post("/v1/measurements", json=batch, headers=hdr).status_code == 200
    # Second batch from the same token within the window is refused.
    assert client.post("/v1/measurements", json=batch, headers=hdr).status_code == 429
