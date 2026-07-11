"""Dish Doctor service: score a user's own measurements against the model (§6.4).

Reads the user's most-recent download measurements from the serving store, scores
each one against the promoted quantile model's q10/q50 for its (cell, hour,
conditions) via the same in-process inference path the forecast uses, and hands
the aligned arrays to the pure ``evaluate_dish`` statistic. Kept separate from the
route so the DB read and inference wiring is one place and the statistics stay in
``orbitcast_ml.anomaly``.

Serving-time simplification (documented limitation): per-measurement historical
precipitation is not re-joined here — features are scored against a dry-weather
expectation. Rain windows are therefore a known false-positive source, which the
obstruction-first framing (F9) and the honest ``basis`` label are meant to temper.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from orbitcast_core.spatial import cell_centroid
from orbitcast_ml.anomaly import DishVerdict, evaluate_dish
from orbitcast_ml.forecast import build_feature_matrix
from orbitcast_ml.models import ForecastModel
from psycopg import Connection

from .forecast import _SERVE_SOURCE_QUALITY, get_orbital_series, resolve_median, resolve_ookla
from .schemas import DishDoctorResponse

# The recent window the verdict is computed over (§6.4). Only rows carrying a
# download reading count — the browser probe is latency-only (§4.3).
_WINDOW = 50


def _recent_downloads(
    conn: Connection, user_id: str
) -> list[tuple[datetime, int, float, float | None]]:
    """The user's most-recent (ts, cell, dl_mbps, obstruction_pct), newest first."""
    rows = conn.execute(
        "SELECT ts, h3_cell, dl_mbps, obstruction_pct FROM measurements "
        "WHERE user_id = %s AND dl_mbps IS NOT NULL "
        "ORDER BY ts DESC LIMIT %s",
        (user_id, _WINDOW),
    ).fetchall()
    return [(r[0], int(r[1]), float(r[2]), r[3]) for r in rows]


def _q10_q50_by_cell(
    cell: int,
    hours: Sequence[datetime],
    model: ForecastModel,
    satellites: Sequence,
    marts_dir: Path,
) -> tuple[list[float], list[float]]:
    """q10 + q50 download predictions for a cell across the given measurement times."""
    lat, lon = cell_centroid(cell)
    orbital = get_orbital_series(satellites, lat, lon, hours)
    baseline, devices = resolve_ookla(cell, marts_dir)
    cell_median, _basis = resolve_median(cell, marts_dir)
    matrix = build_feature_matrix(
        cell,
        hours,
        precip_by_hour={},  # dry-weather expectation (see module docstring)
        orbital_by_hour=orbital,
        terrestrial_baseline_mbps=baseline,
        devices=devices,
        cell_median=cell_median,
        source_quality=_SERVE_SOURCE_QUALITY,
    )
    preds = model.predict(matrix)
    q10 = [float(v) for v in preds["dl_throughput"][0.1]]
    q50 = [float(v) for v in preds["dl_throughput"][0.5]]
    return q10, q50


def score_dish(
    conn: Connection,
    user_id: str,
    model: ForecastModel,
    satellites: Sequence,
    marts_dir: Path,
) -> DishVerdict:
    """Resolve the §6.4 verdict for one user against the promoted model."""
    rows = _recent_downloads(conn, user_id)
    if not rows:
        return evaluate_dish([], [], [], [], [], basis="latitude_prior")

    # The verdict's basis is that of the cell the user reports from most often.
    modal_cell = Counter(cell for _, cell, _, _ in rows).most_common(1)[0][0]
    _median, basis = resolve_median(modal_cell, marts_dir)

    # Predict per distinct cell (build_feature_matrix is single-cell), then realign
    # to the original measurement order.
    q10_by_row: list[float] = [0.0] * len(rows)
    q50_by_row: list[float] = [0.0] * len(rows)
    by_cell: dict[int, list[int]] = {}
    for i, (_ts, cell, _dl, _obs) in enumerate(rows):
        by_cell.setdefault(cell, []).append(i)
    for cell, idxs in by_cell.items():
        hours = [rows[i][0] for i in idxs]
        q10, q50 = _q10_q50_by_cell(cell, hours, model, satellites, marts_dir)
        for pos, i in enumerate(idxs):
            q10_by_row[i] = q10[pos]
            q50_by_row[i] = q50[pos]

    return evaluate_dish(
        dl_observed=[dl for _, _, dl, _ in rows],
        dl_q10=q10_by_row,
        dl_q50=q50_by_row,
        hours_of_day=[ts.hour for ts, _, _, _ in rows],
        obstruction_pcts=[obs for _, _, _, obs in rows],
        basis=basis,
    )


def to_response(verdict: DishVerdict) -> DishDoctorResponse:
    return DishDoctorResponse(
        verdict=verdict.verdict,
        n_evaluated=verdict.n_evaluated,
        below_q10_count=verdict.below_q10_count,
        distinct_hours_below=verdict.distinct_hours_below,
        p_value=verdict.p_value,
        effect_size_pct=verdict.effect_size_pct,
        median_obstruction_pct=verdict.median_obstruction_pct,
        basis=verdict.basis,
    )
