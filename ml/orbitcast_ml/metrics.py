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


def conformal_offset(
    y_true: Sequence[float],
    lower: Sequence[float],
    upper: Sequence[float],
    target_coverage: float = 0.8,
) -> float:
    """Split-conformal (CQR) offset that recalibrates a quantile band.

    Given held-out labels and the model's raw [lower, upper] band, returns the
    amount to widen each edge by (``lower - offset``, ``upper + offset``) so the
    band achieves at least ``target_coverage`` marginal coverage (Romano et al.,
    2019). The conformity score is the signed distance outside the band; a negative
    offset means the band is too wide and should tighten. The (1 + 1/n) inflation
    gives the finite-sample guarantee and is clamped to full coverage.
    """
    yt = np.asarray(y_true, dtype=float)
    lo = np.asarray(lower, dtype=float)
    hi = np.asarray(upper, dtype=float)
    scores = np.maximum(lo - yt, yt - hi)
    n = scores.size
    level = min(1.0, target_coverage * (1.0 + 1.0 / n))
    return float(np.quantile(scores, level))


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
