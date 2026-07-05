"""GET /v1/forecast route (CLAUDE.md §7.3).

503 before any model is promoted; a well-formed 48 h payload once one is. Providers
are monkeypatched so the route test neither hits the network nor trains LightGBM.
"""

import h3
import numpy as np
from fastapi.testclient import TestClient
from orbitcast_api.main import app
from orbitcast_api.routes import forecast as fc

client = TestClient(app)
_CELL = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 5))


class _FakeModel:
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


def test_forecast_503_when_no_model(monkeypatch):
    monkeypatch.setattr(fc, "load_promoted_model", lambda _root: None)
    resp = client.get(f"/v1/forecast?cell={_CELL}")
    assert resp.status_code == 503


def test_forecast_returns_48h_payload(monkeypatch):
    monkeypatch.setattr(fc, "load_promoted_model", lambda _root: _FakeModel())
    monkeypatch.setattr(fc, "get_satellites", lambda: [])
    monkeypatch.setattr(fc, "get_forecast_series", lambda lat, lon: {})
    monkeypatch.setattr(fc, "get_orbital_series", lambda sats, lat, lon, hours: {})
    monkeypatch.setattr(fc, "resolve_ookla", lambda cell, marts: (float("nan"), float("nan")))
    monkeypatch.setattr(fc, "resolve_median", lambda cell, marts: (float("nan"), "latitude_prior"))
    monkeypatch.setattr(fc, "promoted_version", lambda _root: "vTEST")

    resp = client.get(f"/v1/forecast?cell={_CELL}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cell"] == _CELL
    assert body["basis"] == "latitude_prior"
    assert body["model_version"] == "vTEST"
    assert len(body["horizon"]) == 48
    first = body["horizon"][0]
    assert first["latency"] == {"q10": 20.0, "q50": 30.0, "q90": 45.0}
    assert first["dl"] == {"q10": 80.0, "q50": 120.0, "q90": 150.0}
    assert "precip_mm_h" in first["weather"]
