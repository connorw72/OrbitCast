"""GET /v1/forecast — 48 h latency + throughput forecast (CLAUDE.md §6.3, §7.3).

In-process LightGBM inference over live features. Returns honest `basis` labeling
(cell/region/latitude_prior) so the UI never implies measured data where there is
none. If no model has been promoted yet, responds 503 rather than fabricating one.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from orbitcast_core.spatial import cell_centroid

from ..config import get_settings
from ..forecast import (
    build_forecast,
    get_orbital_series,
    load_promoted_model,
    next_hours,
    promoted_version,
    resolve_median,
    resolve_ookla,
)
from ..satellites import get_satellites
from ..schemas import ForecastHour, ForecastResponse
from ..weather import get_forecast_series

router = APIRouter()


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/v1/forecast")
def forecast(cell: int) -> ForecastResponse:
    settings = get_settings()
    model = load_promoted_model(settings.models_dir)
    if model is None:
        raise HTTPException(status_code=503, detail="Forecast model not available yet")

    lat, lon = cell_centroid(cell)
    now = _now()
    hours = next_hours(now)

    weather_series = get_forecast_series(lat, lon)
    orbital = get_orbital_series(get_satellites(), lat, lon, hours)
    baseline, devices = resolve_ookla(cell, settings.marts_dir)
    cell_median, basis = resolve_median(cell, settings.marts_dir)

    horizon = build_forecast(
        cell,
        now,
        model,
        weather_series=weather_series,
        orbital_by_hour=orbital,
        ookla_baseline=baseline,
        ookla_devices=devices,
        cell_median=cell_median,
        basis=basis,
    )
    return ForecastResponse(
        cell=cell,
        lat=lat,
        lon=lon,
        generated_at=now,
        model_version=promoted_version(settings.models_dir),
        basis=basis,
        horizon=[ForecastHour.model_validate(h) for h in horizon],
    )
