"""Eval metrics + promotion gate (CLAUDE.md §6.4).

The gate is the contract that stops a broken model from shipping: q10-q90
coverage must land in [78%, 82%] and q50 MAE must beat a persistence baseline
(same cell, same hour last week). If it can't beat persistence, the features are
broken — do not promote.
"""

import math

from orbitcast_ml.metrics import coverage, mae, pinball_loss, promotion_decision


def test_pinball_zero_when_perfect():
    assert pinball_loss([10.0, 20.0], [10.0, 20.0], 0.5) == 0.0


def test_pinball_q50_is_half_abs_error():
    # q=0.5 pinball == 0.5 * mean(|error|).
    assert math.isclose(pinball_loss([10.0], [8.0], 0.5), 1.0)


def test_pinball_is_asymmetric_for_high_quantile():
    # q=0.9 penalizes under-prediction (y above pred) far more than over-prediction.
    under = pinball_loss([10.0], [8.0], 0.9)  # residual +2
    over = pinball_loss([8.0], [10.0], 0.9)  # residual -2
    assert math.isclose(under, 1.8)
    assert math.isclose(over, 0.2)


def test_coverage_counts_inclusive_band():
    y = [1.0, 2.0, 3.0, 4.0, 5.0]
    lower = [0.0] * 5
    upper = [3.0] * 5
    # 1, 2, 3 fall within [0, 3] -> 3/5.
    assert math.isclose(coverage(y, lower, upper), 0.6)


def test_mae_basic():
    assert math.isclose(mae([1.0, 2.0, 3.0], [1.0, 4.0, 3.0]), 2.0 / 3.0)


def test_promotion_requires_coverage_in_band_and_beating_persistence():
    # Healthy: coverage in band, q50 MAE below persistence.
    assert promotion_decision(cov=0.80, q50_mae=5.0, persistence_mae=6.0) is True


def test_promotion_rejects_out_of_band_coverage():
    assert promotion_decision(cov=0.70, q50_mae=5.0, persistence_mae=6.0) is False
    assert promotion_decision(cov=0.90, q50_mae=5.0, persistence_mae=6.0) is False


def test_promotion_rejects_when_not_beating_persistence():
    assert promotion_decision(cov=0.80, q50_mae=6.0, persistence_mae=6.0) is False
    assert promotion_decision(cov=0.80, q50_mae=7.0, persistence_mae=6.0) is False


def test_promotion_band_edges_inclusive():
    assert promotion_decision(cov=0.78, q50_mae=5.0, persistence_mae=6.0) is True
    assert promotion_decision(cov=0.82, q50_mae=5.0, persistence_mae=6.0) is True
