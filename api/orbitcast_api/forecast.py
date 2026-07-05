"""Forecast service: in-process inference for GET /v1/forecast (CLAUDE.md §7.1, §7.3).

Loads the promoted LightGBM model from the models volume and, for a requested
cell, assembles 48 hourly feature vectors from live providers (weather, orbital,
Ookla context, the fallback rolling median), predicts the quantile bands, and
shapes the payload. Inference is in-process — no model server (§7.1).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orbitcast_core.orbital import sky_view
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

    Empty best-elevation (no satellite above the mask) is passed as NaN so the
    model treats it as missing rather than a real 0°.
    """
    series: dict[datetime, tuple[float, float]] = {}
    for h in hours:
        view = sky_view(satellites, lat, lon, h)
        max_el = view.max_elevation_deg if view.max_elevation_deg is not None else float("nan")
        series[h] = (float(view.sats_visible), float(max_el))
    return series


def resolve_ookla(cell: int, marts_dir: Path) -> tuple[float, float]:
    """(terrestrial_baseline_mbps, devices) for a cell from the Ookla mart.

    Best-effort: returns (NaN, NaN) if the mart is absent or the cell is missing,
    which the model reads as missing context (§6.3)."""
    rows = _read_mart_rows(marts_dir / "ookla_context.parquet")
    for r in rows:
        if r.get("h3_cell") == cell:
            return float(r.get("terrestrial_baseline_mbps", float("nan"))), float(
                r.get("devices", float("nan"))
            )
    return float("nan"), float("nan")


def resolve_median(cell: int, marts_dir: Path) -> tuple[float, Basis]:
    """Rolling cell median + basis via the hierarchical fallback (§6.3).

    Reads the label-aggregate marts if present; with no labels yet, falls back to
    the latitude prior (NaN median, ``latitude_prior`` basis) so the endpoint is
    usable before any labels exist."""
    stat_rows = _read_mart_rows(marts_dir / "cell_label_stats.parquet")
    prior_rows = _read_mart_rows(marts_dir / "latitude_priors.parquet")
    if not stat_rows and not prior_rows:
        return float("nan"), "latitude_prior"

    lookup = {
        r["h3_cell"]: CellStat(median=float(r["median"]), hours=int(r["hours"])) for r in stat_rows
    }
    lat_prior = {int(r["band"]): float(r["median"]) for r in prior_rows}
    try:
        return resolve_cell_median(cell, lookup, lat_prior, min_hours=_MIN_CELL_HOURS)
    except KeyError:
        # No prior for this latitude band — usable but unquantified.
        return float("nan"), "latitude_prior"


def _read_mart_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    import pyarrow.parquet as pq

    return pq.read_table(str(path)).to_pylist()


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
) -> list[dict]:
    """Assemble features, run inference, and shape the 48 h payload for a cell."""
    hours = next_hours(now)
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
