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
from sgp4.api import Satrec
from skyfield.api import EarthSatellite, load, wgs84

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
