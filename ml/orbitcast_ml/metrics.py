"""Eval metrics and the model promotion gate (CLAUDE.md §6.4).

Quantile forecasts are scored with the pinball (quantile) loss; the q10-q90 band
is checked for calibration via empirical coverage; and the median forecast must
beat a persistence baseline. Promotion is gated on both calibration and skill so
a broken feature pipeline cannot ship.
"""

from collections.abc import Sequence

import numpy as np

# Empirical coverage the q10-q90 band must fall within to count as calibrated.
COVERAGE_BOUNDS: tuple[float, float] = (0.78, 0.82)


def pinball_loss(y_true: Sequence[float], y_pred: Sequence[float], quantile: float) -> float:
    """Mean pinball (quantile) loss for a single quantile level."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    residual = yt - yp
    loss = np.maximum(quantile * residual, (quantile - 1.0) * residual)
    return float(loss.mean())


def coverage(y_true: Sequence[float], lower: Sequence[float], upper: Sequence[float]) -> float:
    """Fraction of observations falling within the inclusive [lower, upper] band."""
    yt = np.asarray(y_true, dtype=float)
    lo = np.asarray(lower, dtype=float)
    hi = np.asarray(upper, dtype=float)
    within = (yt >= lo) & (yt <= hi)
    return float(within.mean())


def mae(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    """Mean absolute error."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    return float(np.abs(yt - yp).mean())


def promotion_decision(
    cov: float,
    q50_mae: float,
    persistence_mae: float,
    coverage_bounds: tuple[float, float] = COVERAGE_BOUNDS,
) -> bool:
    """Promote only if the band is calibrated AND the median beats persistence.

    Band edges are inclusive; the median must strictly beat the persistence
    baseline (tie does not clear the bar).
    """
    lo, hi = coverage_bounds
    calibrated = lo <= cov <= hi
    beats_persistence = q50_mae < persistence_mae
    return calibrated and beats_persistence
