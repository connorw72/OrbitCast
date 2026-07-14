"""Forecast service: in-process inference for GET /v1/forecast (CLAUDE.md §7.1, §7.3).

Loads the promoted LightGBM model from the models volume and, for a requested
cell, assembles 48 hourly feature vectors from live providers (weather, orbital,
Ookla context, the fallback rolling median), predicts the quantile bands, and
shapes the payload. Inference is in-process — no model server (§7.1).
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from orbitcast_core.orbital import sky_view_series
from orbitcast_ml.fallback import Basis, CellStat, resolve_cell_median
from orbitcast_ml.forecast import assemble_payload, build_feature_matrix
from orbitcast_ml.models import ForecastModel
from orbitcast_ml.registry import PROMOTED_POINTER

# A res-5 cell qualifies for "cell" basis at >= 1 week of labeled hours (§6.3).
_MIN_CELL_HOURS = 168

# Best/user source tier — at serve time we predict as if for a user-grade dish.
_SERVE_SOURCE_QUALITY = 4.0
FORECAST_HORIZON_H = 48

_model_memo: dict[str, object] = {"version": None, "model": None}


def next_hours(now: datetime, n: int = FORECAST_HORIZON_H) -> list[datetime]:
    """The next ``n`` UTC hour-boundaries starting at the current hour."""
    start = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return [start + timedelta(hours=i) for i in range(n)]


def promoted_version(models_root: Path) -> str | None:
    pointer = models_root / PROMOTED_POINTER
    return pointer.read_text().strip() if pointer.exists() else None


def get_orbital_series(
    satellites: Sequence,
    lat: float,
    lon: float,
    hours: Sequence[datetime],
) -> dict[datetime, tuple[float, float]]:
    """sats_visible + best elevation per hour via in-process propagation (§4.1).

    All satellites x all hours propagate in one vectorized call — the per-request
    hot path (design doc Part 1a). Empty best-elevation (no satellite above the
    mask) is passed as NaN so the model treats it as missing rather than a real 0°.
    """
    views = sky_view_series(satellites, lat, lon, list(hours))
    series: dict[datetime, tuple[float, float]] = {}
    for h, view in zip(hours, views, strict=True):
        max_el = view.max_elevation_deg if view.max_elevation_deg is not None else float("nan")
        series[h] = (float(view.sats_visible), float(max_el))
    return series


def resolve_ookla(cell: int, marts_dir: Path) -> tuple[float, float]:
    """(terrestrial_baseline_mbps, devices) for a cell from the Ookla mart.

    Best-effort: returns (NaN, NaN) if the mart is absent or the cell is missing,
    which the model reads as missing context (§6.3)."""
    index = _mart_index(marts_dir / "ookla_context.parquet", "ookla", _build_ookla_index)
    return index.get(cell, (float("nan"), float("nan")))


def _build_ookla_index(rows: list[dict]) -> dict[int, tuple[float, float]]:
    return {
        int(r["h3_cell"]): (
            float(r.get("terrestrial_baseline_mbps", float("nan"))),
            float(r.get("devices", float("nan"))),
        )
        for r in rows
        if r.get("h3_cell") is not None
    }


def resolve_median(cell: int, marts_dir: Path) -> tuple[float, Basis]:
    """Rolling cell median + basis via the hierarchical fallback (§6.3).

    Reads the label-aggregate marts if present; with no labels yet, falls back to
    the latitude prior (NaN median, ``latitude_prior`` basis) so the endpoint is
    usable before any labels exist."""
    lookup = _mart_index(marts_dir / "cell_label_stats.parquet", "stats", _build_stats_index)
    lat_prior = _mart_index(marts_dir / "latitude_priors.parquet", "priors", _build_priors_index)
    if not lookup and not lat_prior:
        return float("nan"), "latitude_prior"

    try:
        return resolve_cell_median(cell, lookup, lat_prior, min_hours=_MIN_CELL_HOURS)
    except KeyError:
        # No prior for this latitude band — usable but unquantified.
        return float("nan"), "latitude_prior"


def _build_stats_index(rows: list[dict]) -> dict[int, CellStat]:
    return {r["h3_cell"]: CellStat(median=float(r["median"]), hours=int(r["hours"])) for r in rows}


def _build_priors_index(rows: list[dict]) -> dict[int, float]:
    return {int(r["band"]): float(r["median"]) for r in rows}


# Marts are re-read only when their file changes, and per-cell lookups are dict
# hits, not linear scans (design doc Part 1c) — the same (path, mtime) posture as
# `satellites.load_satellites`. A missing mart memoizes as empty under mtime None,
# so a mart appearing later is picked up.
_mart_rows_memo: dict[Path, tuple[float | None, list[dict]]] = {}
_mart_index_memo: dict[tuple[Path, str], tuple[float | None, object]] = {}


def _read_parquet_rows(path: Path) -> list[dict]:
    import pyarrow.parquet as pq

    return pq.read_table(str(path)).to_pylist()


def _read_mart_rows(path: Path) -> list[dict]:
    mtime = path.stat().st_mtime if path.exists() else None
    hit = _mart_rows_memo.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    rows = _read_parquet_rows(path) if mtime is not None else []
    _mart_rows_memo[path] = (mtime, rows)
    return rows


def _mart_index[T](path: Path, kind: str, build: Callable[[list[dict]], T]) -> T:
    mtime = path.stat().st_mtime if path.exists() else None
    hit = _mart_index_memo.get((path, kind))
    if hit is not None and hit[0] == mtime:
        return cast(T, hit[1])
    view = build(_read_mart_rows(path))
    _mart_index_memo[(path, kind)] = (mtime, view)
    return view


def load_promoted_model(models_root: Path) -> ForecastModel | None:
    """Load the currently promoted model, or None if none is promoted yet.

    Memoized by promoted version so the artifacts load once per promotion.
    """
    pointer = models_root / PROMOTED_POINTER
    if not pointer.exists():
        return None
    version = pointer.read_text().strip()
    if _model_memo["version"] == version and _model_memo["model"] is not None:
        return _model_memo["model"]  # type: ignore[return-value]
    model = ForecastModel.load(models_root / version)
    _model_memo.update(version=version, model=model)
    return model


def build_forecast(
    cell: int,
    now: datetime,
    model: ForecastModel,
    weather_series: Mapping[datetime, float],
    orbital_by_hour: Mapping[datetime, tuple[float, float]],
    ookla_baseline: float,
    ookla_devices: float,
    cell_median: float,
    basis: Basis,
    source_quality: float = _SERVE_SOURCE_QUALITY,
    hours: Sequence[datetime] | None = None,
) -> list[dict]:
    """Assemble features, run inference, and shape the payload for a cell.

    ``hours`` defaults to the full 48 h horizon from ``now``; the cache
    read-through path passes only the hours missing from forecast_cache."""
    hours = list(hours) if hours is not None else next_hours(now)
    matrix = build_feature_matrix(
        cell,
        hours,
        precip_by_hour=weather_series,
        orbital_by_hour=orbital_by_hour,
        terrestrial_baseline_mbps=ookla_baseline,
        devices=ookla_devices,
        cell_median=cell_median,
        source_quality=source_quality,
    )
    preds = model.predict(matrix)
    weather_per_hour = [{"precip_mm_h": float(weather_series.get(h, 0.0))} for h in hours]
    return assemble_payload(hours, preds, basis, weather_per_hour)
