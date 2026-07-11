"""GET /v1/dish-doctor route (CLAUDE.md §6.4, §7.3).

Bearer-authenticated per-user verdict against the promoted model's q10 band. The
model + orbital provider are monkeypatched so the route neither trains LightGBM
nor hits the network; measurements are seeded through the real ingest path so the
DB read is exercised end to end. The headline assertion is the Phase 4 DoD in
miniature: the verdict flips when the dish is fed synthetically degraded data.
"""

import numpy as np
from orbitcast_api.routes import dish_doctor as dd

CELL = 599686042433355775


class _FakeModel:
    """q10 download = 80 Mbps, q50 = 120 Mbps, regardless of features."""

    def predict(self, x):
        n = x.shape[0]
        return {
            "latency": {0.1: np.full(n, 20.0), 0.5: np.full(n, 30.0), 0.9: np.full(n, 45.0)},
            "dl_throughput": {
                0.1: np.full(n, 80.0),
                0.5: np.full(n, 120.0),
                0.9: np.full(n, 150.0),
            },
        }


def _patch_model(monkeypatch, model=None):
    model = model or _FakeModel()
    monkeypatch.setattr(dd, "load_promoted_model", lambda _root: model)
    monkeypatch.setattr(dd, "get_satellites", lambda: [])


def _new_token(client) -> str:
    return client.post("/v1/users", json={}).json()["token"]


def _seed(client, token, *, n, dl):
    measurements = [
        {
            "ts": f"2026-07-{1 + (i % 27):02d}T{i % 24:02d}:00:00Z",
            "h3_cell": CELL,
            "source": "reporter",
            "dl_mbps": dl,
            "latency_ms": 40.0,
            "obstruction_pct": 3.0,
        }
        for i in range(n)
    ]
    resp = client.post(
        "/v1/measurements",
        json={"measurements": measurements},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_503_when_no_model(client, monkeypatch):
    monkeypatch.setattr(dd, "load_promoted_model", lambda _root: None)
    token = _new_token(client)
    resp = client.get("/v1/dish-doctor", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 503


def test_requires_a_bearer_token(client, monkeypatch):
    _patch_model(monkeypatch)
    resp = client.get("/v1/dish-doctor")
    assert resp.status_code == 401


def test_insufficient_data_verdict(client, monkeypatch):
    _patch_model(monkeypatch)
    token = _new_token(client)
    _seed(client, token, n=10, dl=100.0)
    resp = client.get("/v1/dish-doctor", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "insufficient_data"
    assert body["n_evaluated"] == 10


def test_browser_probe_latency_does_not_feed_the_verdict(client, monkeypatch):
    """The browser probe is latency-only (§4.3.2); the verdict scores download
    throughput. A user with many browser readings but no downloads must stay
    ``insufficient_data`` with nothing evaluated — the honest caveat the probe UI
    is built around."""
    _patch_model(monkeypatch)
    token = _new_token(client)
    measurements = [
        {
            "ts": f"2026-07-07T{i % 24:02d}:00:00Z",
            "h3_cell": CELL,
            "source": "browser",
            "latency_ms": 45.0,  # no dl_mbps — latency only
        }
        for i in range(30)
    ]
    resp = client.post(
        "/v1/measurements",
        json={"measurements": measurements},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    resp = client.get("/v1/dish-doctor", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "insufficient_data"
    assert body["n_evaluated"] == 0


def test_healthy_dish(client, monkeypatch):
    _patch_model(monkeypatch)
    token = _new_token(client)
    _seed(client, token, n=30, dl=100.0)  # above q10=80 -> healthy
    resp = client.get("/v1/dish-doctor", headers={"Authorization": f"Bearer {token}"})
    body = resp.json()
    assert body["verdict"] == "healthy"
    assert body["below_q10_count"] == 0
    assert body["median_obstruction_pct"] == 3.0


def test_verdict_flips_on_degraded_feed(client, monkeypatch):
    _patch_model(monkeypatch)
    token = _new_token(client)
    _seed(client, token, n=40, dl=10.0)  # below q10=80 across many hours
    resp = client.get("/v1/dish-doctor", headers={"Authorization": f"Bearer {token}"})
    body = resp.json()
    assert body["verdict"] == "underperforming"
    assert body["below_q10_count"] == 40
    assert body["distinct_hours_below"] >= 3
    assert body["p_value"] < 0.01
    # median 10 vs expected q50 120 -> ~92% below.
    assert 90.0 < body["effect_size_pct"] < 93.0
    assert body["median_obstruction_pct"] == 3.0
