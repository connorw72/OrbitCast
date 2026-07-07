"""GET /v1/map — regional hex aggregates for the forecast map (CLAUDE.md §7.3, §7.4).

For each active res-5 cell we run the same in-process forecast the /v1/forecast
route runs, take the current hour, and pull the requested quantile of the requested
metric; those values are aggregated up to the requested H3 resolution with honest
`basis` provenance. The whole response is cached for one hour (per res+metric+hour)
so visitor volume never multiplies the per-cell work — the in-process caching
posture used elsewhere pre-Postgres (§7.1).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
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
from ..map import active_map_cells, aggregate_to_res, parse_metric
from ..satellites import get_satellites
from ..schemas import MapCell, MapResponse
from ..weather import get_forecast_series

router = APIRouter()

# In-process 1 h cache keyed by (res, metric, hour_utc_iso) → MapResponse (§7.3).
_MAP_CACHE: dict[tuple[int, str, str], MapResponse] = {}


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/v1/map")
def map_view(
    res: int = Query(default=4, ge=0, le=5),
    metric: str = Query(default="dl_q50"),
) -> MapResponse:
    try:
        target, quant = parse_metric(metric)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings = get_settings()
    model = load_promoted_model(settings.models_dir)
    if model is None:
        raise HTTPException(status_code=503, detail="Forecast model not available yet")

    now = _now()
    hour_key = now.replace(minute=0, second=0, microsecond=0).isoformat()
    cache_key = (res, metric, hour_key)
    cached = _MAP_CACHE.get(cache_key)
    if cached is not None:
        return cached

    hours = next_hours(now)
    satellites = get_satellites()
    per_cell: dict[int, tuple[float, str]] = {}
    for cell in active_map_cells(settings.marts_dir):
        lat, lon = cell_centroid(cell)
        weather_series = get_forecast_series(lat, lon)
        orbital = get_orbital_series(satellites, lat, lon, hours)
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
        band = horizon[0][target]
        if band is None:  # target has no labels yet (e.g. throughput pre-M-Lab)
            continue
        per_cell[cell] = (float(band[quant]), basis)

    response = MapResponse(
        res=res,
        metric=metric,
        generated_at=now,
        model_version=promoted_version(settings.models_dir),
        cells=[
            MapCell(cell=str(c["cell"]), value=c["value"], basis=c["basis"], n=c["n"])
            for c in aggregate_to_res(per_cell, res)
        ],
    )
    _MAP_CACHE[cache_key] = response
    return response
