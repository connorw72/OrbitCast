"""Orbital engine: satellites-overhead count, best elevation, nearest range.

Validated against skyfield's own scalar altaz as the oracle (CLAUDE.md §4.1). The
overhead case (observer at the sub-satellite point) is a physical sanity check
independent of the oracle: a satellite straight up must read ~90 deg elevation and
a slant range equal to its altitude.
"""

from datetime import UTC, datetime
from typing import cast

import numpy as np
import pytest
from orbitcast_core.orbital import (
    TERMINAL_MASK_DEG,
    satellites_from_gp,
    sky_view,
)
from sgp4 import exporter
from skyfield.api import EarthSatellite, load, wgs84

_ts = load.timescale()
WHEN = datetime(2024, 7, 5, 12, 0, 0, tzinfo=UTC)

# Real-ish TLEs (epoch near WHEN so propagation is well-conditioned).
_ISS = (
    "1 25544U 98067A   24187.50000000  .00016717  00000-0  30000-3 0  9993",
    "2 25544  51.6400 208.0000 0006703 130.0000 325.0000 15.50000000    05",
)
_STARLINK = (
    "1 44714U 19074B   24187.50000000  .00001000  00000-0  10000-3 0  9991",
    "2 44714  53.0000 100.0000 0001000  90.0000 270.0000 15.06000000    07",
)


def _iss() -> EarthSatellite:
    return EarthSatellite(*_ISS, "ISS", _ts)


def _starlink() -> EarthSatellite:
    return EarthSatellite(*_STARLINK, "STARLINK", _ts)


def _subpoint(sat: EarthSatellite) -> tuple[float, float]:
    sub = wgs84.subpoint(sat.at(_ts.from_datetime(WHEN)))
    return cast(float, sub.latitude.degrees), cast(float, sub.longitude.degrees)


def _oracle(sat: EarthSatellite, lat: float, lon: float) -> tuple[float, float]:
    alt, _az, dist = (sat - wgs84.latlon(lat, lon)).at(_ts.from_datetime(WHEN)).altaz()
    return cast(float, alt.degrees), cast(float, dist.km)


def test_overhead_satellite_is_visible_near_zenith() -> None:
    sat = _iss()
    lat, lon = _subpoint(sat)
    view = sky_view([sat], lat, lon, WHEN)
    assert view.sats_visible == 1
    assert view.max_elevation_deg is not None and view.max_elevation_deg > 85.0
    # Straight up: slant range ~ orbital altitude (ISS ~400-410 km here).
    assert view.min_range_km is not None and 350.0 < view.min_range_km < 500.0


def test_single_satellite_matches_oracle() -> None:
    sat = _iss()
    lat, lon = 47.6, -122.3
    elev, rng = _oracle(sat, lat, lon)
    view = sky_view([sat], lat, lon, WHEN)
    if elev >= TERMINAL_MASK_DEG:
        assert view.sats_visible == 1
        assert view.max_elevation_deg == pytest.approx(elev, abs=1e-6)
        assert view.min_range_km == pytest.approx(rng, abs=1e-6)
    else:
        assert view.sats_visible == 0


def test_satellite_below_mask_is_not_counted() -> None:
    sat = _iss()
    slat, slon = _subpoint(sat)
    lat = -slat
    lon = (slon + 180.0) % 360.0 - 180.0  # antipode -> below the horizon
    view = sky_view([sat], lat, lon, WHEN)
    assert view.sats_visible == 0
    assert view.max_elevation_deg is None
    assert view.min_range_km is None


def test_mask_threshold_is_inclusive() -> None:
    sat = _iss()
    lat, lon = _subpoint(sat)
    elev, _rng = _oracle(sat, lat, lon)
    # Exactly at the mask counts (>=); a hair above does not.
    assert sky_view([sat], lat, lon, WHEN, mask_deg=elev).sats_visible == 1
    assert sky_view([sat], lat, lon, WHEN, mask_deg=elev + 0.001).sats_visible == 0


def test_aggregation_matches_oracle_over_a_set() -> None:
    sats = [_iss(), _starlink()]
    lat, lon = _subpoint(_iss())
    elevs = np.array([_oracle(s, lat, lon)[0] for s in sats])
    ranges = np.array([_oracle(s, lat, lon)[1] for s in sats])
    visible = elevs >= TERMINAL_MASK_DEG

    view = sky_view(sats, lat, lon, WHEN)

    assert view.sats_visible == int(visible.sum())
    if visible.any():
        assert view.max_elevation_deg == pytest.approx(elevs[visible].max(), abs=1e-6)
        assert view.min_range_km == pytest.approx(ranges[visible].min(), abs=1e-6)


def test_satellites_from_gp_roundtrips_a_celestrak_omm_record() -> None:
    # CelesTrak sup-gp JSON records are OMM dicts; sgp4's exporter produces the
    # same shape, so we can round-trip without hitting the network.
    record = exporter.export_omm(_iss().model, "ISS")
    sats = satellites_from_gp([record])
    assert len(sats) == 1
    assert sats[0].name == "ISS"
    t = _ts.from_datetime(WHEN)
    drift = np.linalg.norm(np.array(sats[0].at(t).position.km) - np.array(_iss().at(t).position.km))
    assert drift < 1e-6


def test_satellites_from_gp_skips_uninitializable_records() -> None:
    # Freshly launched objects in the supplemental feed carry synthetic NORAD IDs
    # above sgp4's Alpha-5 limit (339999). These must be skipped, not crash the
    # whole load (observed: 24 of ~10,700 real Starlink records).
    good = exporter.export_omm(_iss().model, "ISS")
    bad = {**exporter.export_omm(_starlink().model, "BAD"), "NORAD_CAT_ID": 799501072}
    sats = satellites_from_gp([bad, good])
    assert len(sats) == 1
    assert sats[0].name == "ISS"
