"""Response models for the API surface (CLAUDE.md §7.3)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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


class TakeawayWindow(BaseModel):
    """One concrete window on the horizon (design spec Part 2): a congestion dip,
    a rain-overlap dip, or the recommended best stretch. ``end`` is exclusive."""

    kind: Literal["congestion", "rain", "best"]
    start: str  # ISO-8601 UTC
    end: str
    severity: Literal["mild", "notable"] | None = None  # congestion/rain only
    detail: str


class Takeaways(BaseModel):
    """Verdict-first translation of the horizon; the server is the single source
    of phrasing and the frontend only renders (design spec Part 2)."""

    verdict: Literal["smooth", "mixed", "rough"]
    headline: str
    confidence: Literal["high", "medium", "low"]
    windows: list[TakeawayWindow]


class ForecastResponse(BaseModel):
    """48 h latency + download-throughput forecast with uncertainty bands (§7.3)."""

    cell: int
    lat: float
    lon: float
    generated_at: datetime
    model_version: str | None
    basis: str  # the resolved fallback level for this cell
    takeaways: Takeaways
    horizon: list[ForecastHour]


class MapCell(BaseModel):
    """One aggregated hex for the regional map (§7.4): the metric value, the best
    provenance among the cell's children, and how many res-5 cells rolled into it.

    ``cell`` is the 64-bit H3 id as a decimal *string* — it exceeds JS Number
    precision, so it must survive JSON as text (same reason the client sends cells
    as strings in requests)."""

    cell: str
    value: float
    basis: str
    n: int


class MapResponse(BaseModel):
    """Cell aggregates for the deck-style hex map (§7.3 GET /v1/map)."""

    res: int
    metric: str
    generated_at: datetime
    model_version: str | None
    cells: list[MapCell]


class UserCreate(BaseModel):
    """Mint an anonymous user. The client sends only its res-5 cell, never raw
    coordinates (D12); the cell is optional so a user can register before choosing
    a location."""

    h3_cell: int | None = None


class UserCreated(BaseModel):
    """The token is returned exactly once — the server keeps only its hash (§7.2)."""

    user_id: str
    token: str
    h3_cell: int | None


class MeasurementIn(BaseModel):
    """One crowdsourced sample (§4.3). Locations are res-5 cells chosen client-side.
    Latency-only for the browser probe; the reporter also carries throughput,
    obstruction, and hardware version."""

    ts: datetime
    h3_cell: int
    source: Literal["reporter", "browser"]
    latency_ms: float | None = Field(default=None, ge=0)
    dl_mbps: float | None = Field(default=None, ge=0)
    ul_mbps: float | None = Field(default=None, ge=0)
    obstruction_pct: float | None = Field(default=None, ge=0, le=100)
    hw_version: str | None = None


class MeasurementBatch(BaseModel):
    """A batch ingest (§7.3). Bounded so one request can't dump unbounded rows."""

    measurements: list[MeasurementIn] = Field(min_length=1, max_length=1000)


class MeasurementBatchResult(BaseModel):
    accepted: int


class DishDoctorResponse(BaseModel):
    """Per-user underperformance verdict (§6.4). Interpretable evidence, not an
    accusation: the dish's own median obstruction is surfaced first (F9), and the
    verdict states which data basis it was judged against."""

    verdict: str  # "insufficient_data" | "healthy" | "underperforming"
    n_evaluated: int
    below_q10_count: int
    distinct_hours_below: int
    p_value: float | None
    effect_size_pct: float | None  # % median download below the model's expectation
    median_obstruction_pct: float | None
    basis: str  # "cell" | "region" | "latitude_prior"


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
