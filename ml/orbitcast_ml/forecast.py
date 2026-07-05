"""Serving-time forecast assembly + inference (CLAUDE.md §6.3, §7.1, §7.3).

Builds the 48 h feature matrix for a cell in exactly FEATURE_COLUMNS order (parity
with the training-matrix builder), runs in-process LightGBM inference, and shapes
the API payload. Feature *sources* (live weather, orbital counts, Ookla context,
the fallback median + basis) are injected by the route so this core stays pure and
testable.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

import numpy as np
from numpy.typing import NDArray
from orbitcast_core.spatial import cell_centroid

from .fallback import Basis
from .features import FEATURE_COLUMNS, time_features
from .models import QUANTILES


def build_feature_matrix(
    cell: int,
    hours: Sequence[datetime],
    precip_by_hour: Mapping[datetime, float],
    orbital_by_hour: Mapping[datetime, tuple[float, float]],
    terrestrial_baseline_mbps: float,
    devices: float,
    cell_median: float,
    source_quality: float,
) -> NDArray[np.float64]:
    """Assemble the (len(hours), len(FEATURE_COLUMNS)) matrix for a cell.

    ``precip_by_hour`` is the hourly precip series; precip_lag_1h and
    precip_forecast_3h are derived from it (missing hours read as 0). ``cell_median``
    and ``source_quality`` are the fallback-resolved rolling median and the tier we
    predict at (best/user tier at serve time).
    """
    lat, lon = cell_centroid(cell)
    rows: list[list[float]] = []
    for h in hours:
        sats, max_el = orbital_by_hour.get(h, (float("nan"), float("nan")))
        tf = time_features(h, lon)
        row = {
            **tf,
            "precip_mm_h": precip_by_hour.get(h, 0.0),
            "precip_lag_1h": precip_by_hour.get(h - timedelta(hours=1), 0.0),
            "precip_forecast_3h": precip_by_hour.get(h + timedelta(hours=3), 0.0),
            "sats_visible": sats,
            "max_elevation_deg": max_el,
            "cell_lat": lat,
            "terrestrial_baseline_mbps": terrestrial_baseline_mbps,
            "devices": devices,
            "cell_median_7d": cell_median,
            "source_quality": source_quality,
        }
        rows.append([float(row[col]) for col in FEATURE_COLUMNS])
    return np.array(rows, dtype=float)


def assemble_payload(
    hours: Sequence[datetime],
    preds: Mapping[str, Mapping[float, Sequence[float] | NDArray[np.float64]]],
    basis: Basis,
    weather_per_hour: Sequence[Mapping],
) -> list[dict]:
    """Shape per-hour predictions into the /v1/forecast payload (§7.3).

    Each entry carries the hour, the latency/dl quantile bands, the resolved
    ``basis`` (honest data-provenance labeling), and that hour's weather.
    """
    q10, q50, q90 = QUANTILES

    def band(target: str, i: int) -> dict | None:
        # A target with no labels yet (e.g. throughput pre-M-Lab) is absent.
        if target not in preds:
            return None
        return {
            "q10": float(preds[target][q10][i]),
            "q50": float(preds[target][q50][i]),
            "q90": float(preds[target][q90][i]),
        }

    out: list[dict] = []
    for i, h in enumerate(hours):
        out.append(
            {
                "hour": h.isoformat(),
                "basis": basis,
                "latency": band("latency", i),
                "dl": band("dl_throughput", i),
                "weather": dict(weather_per_hour[i]),
            }
        )
    return out
