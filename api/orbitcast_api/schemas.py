"""Response models for the API surface (CLAUDE.md §7.3)."""

from datetime import datetime

from pydantic import BaseModel


class WeatherNow(BaseModel):
    precip_mm_h: float
    cloud_cover_pct: float
    snow_mm_h: float


class SkyviewResponse(BaseModel):
    """Deterministic sky view for a location (no ML). Supply proxies only — we do
    not claim which satellite serves the user (F3)."""

    cell: int
    lat: float
    lon: float
    sats_visible: int
    max_elevation_deg: float | None
    min_range_km: float | None
    seconds_to_reconfig: float
    next_reconfig: datetime
    schedule_seconds: list[int]
    server_time: datetime
    gp_fetched_at: datetime | None  # freshness of the orbital data (F5)
    weather: WeatherNow | None
