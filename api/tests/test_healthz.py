"""Phase 0 DoD: healthz responds with {"status": "ok"}."""

from fastapi.testclient import TestClient
from orbitcast_api.main import app

client = TestClient(app)


def test_healthz_returns_ok() -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
