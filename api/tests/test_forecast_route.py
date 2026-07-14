"""GET /v1/forecast route (CLAUDE.md §7.3).

503 before any model is promoted; a well-formed 48 h payload once one is. Providers
are monkeypatched so the route test neither hits the network nor trains LightGBM.
"""

from datetime import UTC, datetime

import h3
import numpy as np
from fastapi.testclient import TestClient
from orbitcast_api.forecast import next_hours
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


def _stub_providers(monkeypatch, cached=None):
    monkeypatch.setattr(fc, "load_promoted_model", lambda _root: _FakeModel())
    monkeypatch.setattr(fc, "get_satellites", lambda: [])
    monkeypatch.setattr(fc, "get_forecast_series_cached", lambda cell, lat, lon, now: {})
    monkeypatch.setattr(fc, "get_orbital_series", lambda sats, lat, lon, hours: {})
    monkeypatch.setattr(fc, "resolve_ookla", lambda cell, marts: (float("nan"), float("nan")))
    monkeypatch.setattr(fc, "resolve_median", lambda cell, marts: (float("nan"), "latitude_prior"))
    monkeypatch.setattr(fc, "promoted_version", lambda _root: "vTEST")
    monkeypatch.setattr(fc, "read_cached", lambda cell, hours, version: dict(cached or {}))
    written: list = []
    monkeypatch.setattr(fc, "write_through", lambda cell, version, payload: written.append(payload))
    return written


def test_forecast_returns_48h_payload(monkeypatch):
    _stub_providers(monkeypatch)

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


# --- forecast_cache read-through (design doc Part 1b) ---------------------------
# Cached hours must serve without recomputing; only missing hours are computed and
# written back. `_now` is pinned so the test's hour grid matches the route's.

_NOW = datetime(2026, 7, 13, 12, 30, tzinfo=UTC)


def _cached_entry(hour):
    return {
        "basis": "cell",
        "latency": {"q10": 11.0, "q50": 22.0, "q90": 33.0},
        "dl": {"q10": 44.0, "q50": 55.0, "q90": 66.0},
    }


def test_fully_cached_horizon_serves_without_compute(monkeypatch):
    monkeypatch.setattr(fc, "_now", lambda: _NOW)
    hours = next_hours(_NOW)
    written = _stub_providers(monkeypatch, cached={h: _cached_entry(h) for h in hours})

    def explode(*a, **k):
        raise AssertionError("compute path must not run when fully cached")

    monkeypatch.setattr(fc, "get_orbital_series", explode)
    monkeypatch.setattr(fc, "build_forecast", explode)

    resp = client.get(f"/v1/forecast?cell={_CELL}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["horizon"]) == 48
    assert body["horizon"][0]["latency"] == {"q10": 11.0, "q50": 22.0, "q90": 33.0}
    assert body["horizon"][0]["basis"] == "cell"
    assert written == []  # nothing recomputed, nothing rewritten


def test_partial_cache_computes_and_writes_back_only_missing_hours(monkeypatch):
    monkeypatch.setattr(fc, "_now", lambda: _NOW)
    hours = next_hours(_NOW)
    cached = {h: _cached_entry(h) for h in hours[1:]}  # first hour missing
    written = _stub_providers(monkeypatch, cached=cached)

    seen_hours: list = []
    monkeypatch.setattr(
        fc,
        "get_orbital_series",
        lambda sats, lat, lon, hrs: (seen_hours.extend(hrs), {})[1],
    )

    resp = client.get(f"/v1/forecast?cell={_CELL}")
    assert resp.status_code == 200
    assert seen_hours == [hours[0]]  # propagation ran for the missing hour only
    assert len(written) == 1 and len(written[0]) == 1
    body = resp.json()
    assert body["horizon"][0]["latency"]["q50"] == 30.0  # computed (fake model)
    assert body["horizon"][1]["latency"]["q50"] == 22.0  # cached
    assert [h["hour"] for h in body["horizon"]] == [h.isoformat() for h in hours]
