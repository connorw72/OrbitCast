"""GET /v1/map route (CLAUDE.md §7.3, §7.4).

503 before any model is promoted; a well-formed set of aggregated hex cells once
one is. Providers, the model, and the active-cell set are monkeypatched so the
route test neither hits the network nor trains LightGBM.
"""

import h3
import numpy as np
import pytest
from fastapi.testclient import TestClient
from orbitcast_api.main import app
from orbitcast_api.routes import map as mp

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_cache():
    mp._MAP_CACHE.clear()
    yield
    mp._MAP_CACHE.clear()


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


def _two_children_of_one_parent() -> tuple[int, int, int]:
    parent = h3.cell_to_parent(h3.latlng_to_cell(52.28, 8.05, 5), 4)
    a, b = sorted(h3.cell_to_children(parent, 5))[:2]
    return h3.str_to_int(a), h3.str_to_int(b), h3.str_to_int(parent)


def _stub_providers(monkeypatch, cached=None):
    monkeypatch.setattr(mp, "weather_hour_index", lambda marts: {})
    monkeypatch.setattr(mp, "orbital_hour_index", lambda marts: {})
    monkeypatch.setattr(mp, "resolve_ookla", lambda cell, marts: (float("nan"), float("nan")))
    monkeypatch.setattr(mp, "resolve_median", lambda cell, marts: (float("nan"), "latitude_prior"))
    monkeypatch.setattr(mp, "promoted_version", lambda _root: "vTEST")
    monkeypatch.setattr(mp, "read_cached_many", lambda cells, hour, version: dict(cached or {}))
    written: list = []
    monkeypatch.setattr(mp, "write_through_many", lambda version, items: written.extend(items))
    return written


def test_map_503_when_no_model(monkeypatch):
    monkeypatch.setattr(mp, "load_promoted_model", lambda _root: None)
    resp = client.get("/v1/map")
    assert resp.status_code == 503


def test_map_400_on_unknown_metric(monkeypatch):
    monkeypatch.setattr(mp, "load_promoted_model", lambda _root: _FakeModel())
    resp = client.get("/v1/map?metric=upload_q50")
    assert resp.status_code == 400


def test_map_aggregates_active_cells_to_res4(monkeypatch):
    a, b, parent = _two_children_of_one_parent()
    monkeypatch.setattr(mp, "load_promoted_model", lambda _root: _FakeModel())
    monkeypatch.setattr(mp, "active_map_cells", lambda marts: {a, b})
    _stub_providers(monkeypatch)

    resp = client.get("/v1/map?res=4&metric=dl_q50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["res"] == 4
    assert body["metric"] == "dl_q50"
    assert body["model_version"] == "vTEST"
    assert len(body["cells"]) == 1
    cell = body["cells"][0]
    assert cell["cell"] == str(parent)  # 64-bit id survives JSON as a decimal string
    assert cell["value"] == pytest.approx(120.0)  # q50 dl from the fake model
    assert cell["n"] == 2
    assert cell["basis"] == "latitude_prior"


def test_map_serves_cached_cells_without_compute(monkeypatch):
    # A cell whose current hour is already in forecast_cache must not re-run
    # inference (design doc Part 1b: the map reads through the same cache).
    a, _b, parent = _two_children_of_one_parent()
    monkeypatch.setattr(mp, "load_promoted_model", lambda _root: _FakeModel())
    monkeypatch.setattr(mp, "active_map_cells", lambda marts: {a})
    cached = {
        a: {
            "basis": "cell",
            "latency": {"q10": 11.0, "q50": 22.0, "q90": 33.0},
            "dl": {"q10": 44.0, "q50": 55.0, "q90": 66.0},
        }
    }
    written = _stub_providers(monkeypatch, cached=cached)

    def explode(*args, **kwargs):
        raise AssertionError("cached cell must not recompute")

    monkeypatch.setattr(mp, "build_feature_matrix", explode)

    resp = client.get("/v1/map?res=4&metric=dl_q50")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["cells"]) == 1
    assert body["cells"][0]["cell"] == str(parent)
    assert body["cells"][0]["value"] == pytest.approx(55.0)
    assert body["cells"][0]["basis"] == "cell"
    assert written == []


def test_map_defaults_and_empty_active_set(monkeypatch):
    monkeypatch.setattr(mp, "load_promoted_model", lambda _root: _FakeModel())
    monkeypatch.setattr(mp, "active_map_cells", lambda marts: set())
    _stub_providers(monkeypatch)

    resp = client.get("/v1/map")
    assert resp.status_code == 200
    body = resp.json()
    assert body["res"] == 4  # documented default
    assert body["metric"] == "dl_q50"  # documented default
    assert body["cells"] == []
