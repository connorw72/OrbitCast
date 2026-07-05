"""Response models for the API surface (CLAUDE.md §7.3)."""

from datetime import datetime

from pydantic import BaseModel


class WeatherNow(BaseModel):
    precip_mm_h: float
    cloud_cover_pct: float
    snow_mm_h: float


class QuantileBand(BaseModel):
    """q10/q50/q90 uncertainty band for one metric at one hour (§6.2)."""

    q10: float
    q50: float
    q90: float


class ForecastHour(BaseModel):
    hour: str  # ISO-8601 UTC
    basis: str  # "cell" | "region" | "latitude_prior" (§6.3 honest provenance)
    # A target is null until it has labels (throughput waits on M-Lab; §4.2a).
    latency: QuantileBand | None
    dl: QuantileBand | None
    weather: dict


class ForecastResponse(BaseModel):
    """48 h latency + download-throughput forecast with uncertainty bands (§7.3)."""

    cell: int
    lat: float
    lon: float
    generated_at: datetime
    model_version: str | None
    basis: str  # the resolved fallback level for this cell
    horizon: list[ForecastHour]


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
