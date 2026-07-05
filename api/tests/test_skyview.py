"""GET /v1/skyview — deterministic sky view (CLAUDE.md §7.3, Phase 1 DoD).

The route is wired with monkeypatchable satellites, weather, and clock so the test
is fully deterministic and never touches the network.
"""

from datetime import UTC, datetime

import h3
import pytest
from fastapi.testclient import TestClient
from orbitcast_api.main import app
from orbitcast_api.routes import skyview as skyview_mod
from orbitcast_api.schemas import WeatherNow
from orbitcast_core.orbital import sky_view
from orbitcast_core.spatial import cell_centroid
from skyfield.api import EarthSatellite, load

client = TestClient(app)
_ts = load.timescale()
_ISS = (
    "1 25544U 98067A   24187.50000000  .00016717  00000-0  30000-3 0  9993",
    "2 25544  51.6400 208.0000 0006703 130.0000 325.0000 15.50000000    05",
)
_FIXED = datetime(2024, 7, 5, 12, 0, 20, tzinfo=UTC)  # :20 -> next reconfig at :27
_SEATTLE_CELL = h3.str_to_int(h3.latlng_to_cell(47.6, -122.3, 5))


def _wire(monkeypatch, sats, weather=None) -> None:
    monkeypatch.setattr(skyview_mod, "get_satellites", lambda: sats)
    monkeypatch.setattr(skyview_mod, "get_nowcast", lambda *a, **k: weather)
    monkeypatch.setattr(skyview_mod, "gp_fetched_at", lambda: None)
    monkeypatch.setattr(skyview_mod, "_now", lambda: _FIXED)


def test_skyview_shape_schedule_and_countdown(monkeypatch) -> None:
    sats = [EarthSatellite(*_ISS, "ISS", _ts)]
    _wire(monkeypatch, sats)

    resp = client.get(f"/v1/skyview?cell={_SEATTLE_CELL}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cell"] == _SEATTLE_CELL
    assert body["schedule_seconds"] == [12, 27, 42, 57]
    assert body["seconds_to_reconfig"] == pytest.approx(7.0)  # 27 - 20
    assert body["next_reconfig"].startswith("2024-07-05T12:00:27")
    lat, lon = cell_centroid(_SEATTLE_CELL)
    assert body["lat"] == pytest.approx(lat)
    assert body["lon"] == pytest.approx(lon)
    # sats_visible agrees with a direct orbital recompute at the same instant.
    assert body["sats_visible"] == sky_view(sats, lat, lon, _FIXED).sats_visible


def test_skyview_with_no_satellites_is_still_ok(monkeypatch) -> None:
    _wire(monkeypatch, [])
    body = client.get(f"/v1/skyview?cell={_SEATTLE_CELL}").json()
    assert body["sats_visible"] == 0
    assert body["max_elevation_deg"] is None
    assert body["min_range_km"] is None


def test_skyview_includes_weather_when_present(monkeypatch) -> None:
    weather = WeatherNow(precip_mm_h=1.2, cloud_cover_pct=75.0, snow_mm_h=0.0)
    _wire(monkeypatch, [EarthSatellite(*_ISS, "ISS", _ts)], weather=weather)
    body = client.get(f"/v1/skyview?cell={_SEATTLE_CELL}").json()
    assert body["weather"]["precip_mm_h"] == 1.2
    assert body["weather"]["cloud_cover_pct"] == 75.0
