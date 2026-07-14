"""GET /v1/skyview — deterministic sky view (CLAUDE.md §4.1, §7.3).

Everything here is arithmetic + orbital propagation; no ML. The 15-second
reconfiguration countdown is a global clock computed at request time, and the
per-second ticking happens client-side, so this endpoint is called once per page
load rather than every second.
"""

from datetime import UTC, datetime

from fastapi import APIRouter
from orbitcast_core.orbital import sky_view_series
from orbitcast_core.schedule import RECONFIG_SECONDS, next_reconfig, seconds_to_reconfig
from orbitcast_core.spatial import cell_centroid

from ..satellites import get_satellites, gp_fetched_at
from ..schemas import SkyviewResponse
from ..weather import get_nowcast

router = APIRouter()


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/v1/skyview")
def skyview(cell: int) -> SkyviewResponse:
    lat, lon = cell_centroid(cell)
    now = _now()
    [view] = sky_view_series(get_satellites(), lat, lon, [now])
    return SkyviewResponse(
        cell=cell,
        lat=lat,
        lon=lon,
        sats_visible=view.sats_visible,
        max_elevation_deg=view.max_elevation_deg,
        min_range_km=view.min_range_km,
        seconds_to_reconfig=seconds_to_reconfig(now),
        next_reconfig=next_reconfig(now),
        schedule_seconds=list(RECONFIG_SECONDS),
        server_time=now,
        gp_fetched_at=gp_fetched_at(),
        weather=get_nowcast(cell, lat, lon, now),
    )
