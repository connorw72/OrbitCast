"""Orbital engine (CLAUDE.md §4.1).

Propagates Starlink GP data with skyfield (sgp4 under the hood) to derive the
supply-side sky features for a location: how many satellites are above the
terminal elevation mask, the best elevation, and the nearest slant range.

These are *supply proxies* only — we cannot know which satellite serves a user
(failure mode F3). Product copy must say "satellites overhead", never "your
satellite".
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import numpy as np
from sgp4 import omm
from sgp4.api import Satrec, SatrecArray, jday
from skyfield.api import EarthSatellite, load, wgs84
from skyfield.sgp4lib import theta_GMST1982

# Starlink user-terminal elevation mask: satellites below 25 deg are not usable.
TERMINAL_MASK_DEG = 25.0

_ts = load.timescale()


@dataclass(frozen=True)
class SkyView:
    """Deterministic sky summary for a location at an instant."""

    sats_visible: int
    max_elevation_deg: float | None
    min_range_km: float | None


def satellites_from_gp(records: Sequence[dict]) -> list[EarthSatellite]:
    """Build skyfield satellites from CelesTrak GP (OMM JSON) records.

    Records that cannot be initialized are skipped rather than aborting the whole
    load: freshly launched objects in the supplemental feed carry synthetic NORAD
    IDs above sgp4's Alpha-5 limit (339999) and raise ValueError. These are a
    negligible fraction of the constellation and do not affect visible counts.
    """
    satellites: list[EarthSatellite] = []
    for record in records:
        satrec = Satrec()
        try:
            omm.initialize(satrec, record)
        except ValueError:
            continue
        sat = EarthSatellite.from_satrec(satrec, _ts)
        sat.name = record.get("OBJECT_NAME")
        satellites.append(sat)
    return satellites


def sky_view(
    satellites: Sequence[EarthSatellite],
    lat: float,
    lon: float,
    when: datetime,
    mask_deg: float = TERMINAL_MASK_DEG,
) -> SkyView:
    """Summarize visible satellites over (lat, lon) at `when`.

    A satellite counts as visible when its elevation is >= `mask_deg`. Among the
    visible set, report the best elevation and the nearest slant range.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    t = _ts.from_datetime(when.astimezone(UTC))
    observer = wgs84.latlon(lat, lon)

    elevations = np.empty(len(satellites))
    ranges = np.empty(len(satellites))
    for i, sat in enumerate(satellites):
        alt, _az, dist = (sat - observer).at(t).altaz()
        # skyfield ships no usable stubs, so cast at the boundary.
        elevations[i] = cast(float, alt.degrees)
        ranges[i] = cast(float, dist.km)

    visible = elevations >= mask_deg
    count = int(visible.sum())
    if count == 0:
        return SkyView(0, None, None)
    return SkyView(
        sats_visible=count,
        max_elevation_deg=float(elevations[visible].max()),
        min_range_km=float(ranges[visible].min()),
    )


def sky_view_series(
    satellites: Sequence[EarthSatellite],
    lat: float,
    lon: float,
    times: Sequence[datetime],
    mask_deg: float = TERMINAL_MASK_DEG,
) -> list[SkyView]:
    """Vectorized `sky_view` over a whole time series in one sgp4 call.

    Propagates all satellites x all timesteps with sgp4's SatrecArray (C loop),
    then TEME -> PEF via the GMST rotation and PEF -> local ENU with numpy array
    math — no per-satellite Python loop (CLAUDE.md §4.1). Polar motion is ignored,
    which matches the scalar path under the builtin timescale; agreement with the
    scalar skyfield oracle is within ±0.2 deg elevation / ±5 km range (tested).

    Satellites whose propagation fails at a timestep (decayed objects, sgp4 error
    codes) are treated as not visible at that timestep rather than aborting.
    """
    if len(times) == 0:
        return []
    aware = [(w if w.tzinfo is not None else w.replace(tzinfo=UTC)).astimezone(UTC) for w in times]
    if len(satellites) == 0:
        return [SkyView(0, None, None) for _ in aware]

    # sgp4 takes UTC julian dates (TLE epochs are UTC per AIAA 2006-6753).
    jd = np.empty(len(aware))
    fr = np.empty(len(aware))
    for i, w in enumerate(aware):
        seconds = w.second + w.microsecond / 1e6
        jd[i], fr[i] = jday(w.year, w.month, w.day, w.hour, w.minute, seconds)
    err, pos, _vel = SatrecArray([sat.model for sat in satellites]).sgp4(jd, fr)

    # TEME -> PEF is ROT3(GMST1982); same angle skyfield's TEME frame uses.
    t = _ts.from_datetimes(aware)
    # skyfield ships no usable stubs (same boundary cast as sky_view above).
    whole = np.atleast_1d(cast(np.ndarray, t.whole))
    ut1_fraction = np.atleast_1d(cast(np.ndarray, t.ut1_fraction))
    # theta_GMST1982 is annotated scalar-only but is plain numpy arithmetic and
    # skyfield itself calls it with arrays; cast at the same boundary.
    theta, _theta_dot = theta_GMST1982(whole, cast(float, ut1_fraction))
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    x_teme, y_teme, z = pos[..., 0], pos[..., 1], pos[..., 2]  # (n_sat, n_time)
    x = cos_t * x_teme + sin_t * y_teme
    y = -sin_t * x_teme + cos_t * y_teme

    ox, oy, oz = wgs84.latlon(lat, lon).itrs_xyz.km
    dx, dy, dz = x - ox, y - oy, z - oz

    lat_r, lon_r = np.radians(lat), np.radians(lon)
    sin_lat, cos_lat = np.sin(lat_r), np.cos(lat_r)
    sin_lon, cos_lon = np.sin(lon_r), np.cos(lon_r)
    # Geodetic "up" at the observer; elevation = angle of the topocentric vector
    # above the horizon plane.
    up = dx * cos_lat * cos_lon + dy * cos_lat * sin_lon + dz * sin_lat
    ranges = np.sqrt(dx * dx + dy * dy + dz * dz)

    bad = (err != 0) | ~np.isfinite(ranges) | (ranges == 0.0)
    ranges = np.where(bad, np.inf, ranges)
    with np.errstate(invalid="ignore"):
        elevations = np.degrees(np.arcsin(np.clip(up / ranges, -1.0, 1.0)))
    elevations = np.where(bad, -np.inf, elevations)

    out: list[SkyView] = []
    for j in range(len(aware)):
        col = elevations[:, j]
        visible = col >= mask_deg
        count = int(visible.sum())
        if count == 0:
            out.append(SkyView(0, None, None))
        else:
            out.append(
                SkyView(
                    sats_visible=count,
                    max_elevation_deg=float(col[visible].max()),
                    min_range_km=float(ranges[visible, j].min()),
                )
            )
    return out
