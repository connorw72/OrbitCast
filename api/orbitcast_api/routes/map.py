"""GET /v1/map — regional hex aggregates for the forecast map (CLAUDE.md §7.3, §7.4).

For each active res-5 cell we run the same in-process forecast the /v1/forecast
route runs, take the current hour, and pull the requested quantile of the requested
metric; those values are aggregated up to the requested H3 resolution with honest
`basis` provenance. The whole response is cached for one hour (per res+metric+hour)
so visitor volume never multiplies the per-cell work — the in-process caching
posture used elsewhere pre-Postgres (§7.1).
"""

from datetime import UTC, datetime

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from orbitcast_ml.forecast import build_feature_matrix
from orbitcast_ml.models import QUANTILES

from ..config import get_settings
from ..forecast import (
    _SERVE_SOURCE_QUALITY,
    load_promoted_model,
    orbital_hour_index,
    promoted_version,
    resolve_median,
    resolve_ookla,
    weather_hour_index,
)
from ..forecast_cache import read_cached_many, write_through_many
from ..map import active_map_cells, aggregate_to_res, parse_metric
from ..schemas import MapCell, MapResponse

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

    # The map needs only the current hour per cell, reading through the same
    # forecast_cache /v1/forecast fills (design spec Part 1b) — but at thousands
    # of active cells it must stay batched: one cache read, mart lookups for
    # weather/orbital (the pipeline precomputes these per active cell, §5.3 —
    # never per-cell HTTP or propagation here), one model predict, one write-back.
    hour = now.replace(minute=0, second=0, microsecond=0)
    version = promoted_version(settings.models_dir)
    cells = sorted(active_map_cells(settings.marts_dir))
    cached = read_cached_many(cells, hour, version) if version is not None else {}
    missing = [c for c in cells if c not in cached]

    entries: dict[int, dict] = dict(cached)
    if missing:
        weather_idx = weather_hour_index(settings.marts_dir)
        orbital_idx = orbital_hour_index(settings.marts_dir)
        nan = float("nan")
        matrices = []
        metas: list[tuple[int, str, float]] = []
        for cell in missing:
            baseline, devices = resolve_ookla(cell, settings.marts_dir)
            cell_median, basis = resolve_median(cell, settings.marts_dir)
            precip = weather_idx.get((cell, hour), 0.0)
            orbital = orbital_idx.get((cell, hour), (nan, nan))
            matrices.append(
                build_feature_matrix(
                    cell,
                    [hour],
                    precip_by_hour={hour: precip},
                    orbital_by_hour={hour: orbital},
                    terrestrial_baseline_mbps=baseline,
                    devices=devices,
                    cell_median=cell_median,
                    source_quality=_SERVE_SOURCE_QUALITY,
                )
            )
            metas.append((cell, basis, precip))
        preds = model.predict(np.vstack(matrices))
        q10, q50, q90 = QUANTILES

        def band_at(pred_target: str, i: int) -> dict | None:
            if pred_target not in preds:
                return None
            p = preds[pred_target]
            return {"q10": float(p[q10][i]), "q50": float(p[q50][i]), "q90": float(p[q90][i])}

        computed: list[tuple[int, dict]] = []
        for i, (cell, basis, precip) in enumerate(metas):
            entry = {
                "hour": hour.isoformat(),
                "basis": basis,
                "latency": band_at("latency", i),
                "dl": band_at("dl_throughput", i),
                "weather": {"precip_mm_h": precip},
            }
            computed.append((cell, entry))
            entries[cell] = entry
        if version is not None:
            write_through_many(version, computed)

    per_cell: dict[int, tuple[float, str]] = {}
    for cell, entry in entries.items():
        band = entry[target]
        if band is None:  # target has no labels yet (e.g. throughput pre-M-Lab)
            continue
        per_cell[cell] = (float(band[quant]), entry["basis"])

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
