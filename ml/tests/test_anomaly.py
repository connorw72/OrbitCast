"""Dish Doctor per-user anomaly test (CLAUDE.md §6.4).

Underperformance is a statistical test against the model's q10 band, not an
autoencoder: under a healthy dish, an observation falls below the (cell, hour)
q10 with probability 0.1. A one-sided binomial test over the recent window,
gated on the drop being sustained across >=3 distinct hours-of-day, flags a
genuinely underperforming dish while ignoring a single bad evening (F9).
"""

import math
from typing import Any

from orbitcast_ml.anomaly import (
    ALPHA,
    MIN_DISTINCT_HOURS,
    MIN_MEASUREMENTS,
    binomial_sf,
    evaluate_dish,
)

# --- binomial_sf: one-sided upper tail P[X >= k] for X ~ Binomial(n, p) ---


def test_binomial_sf_full_mass_at_zero():
    # P[X >= 0] == 1 for any n, p.
    assert math.isclose(binomial_sf(0, 10, 0.1), 1.0)


def test_binomial_sf_single_trial():
    # P[X >= 1] over one trial == p.
    assert math.isclose(binomial_sf(1, 1, 0.1), 0.1)


def test_binomial_sf_matches_hand_computation():
    # n=3, p=0.5, P[X >= 2] = C(3,2)/8 + C(3,3)/8 = 3/8 + 1/8 = 0.5.
    assert math.isclose(binomial_sf(2, 3, 0.5), 0.5)


def test_binomial_sf_all_successes():
    # P[X >= n] == p**n.
    assert math.isclose(binomial_sf(4, 4, 0.1), 0.1**4)


def test_binomial_sf_is_decreasing_in_k():
    assert binomial_sf(2, 20, 0.1) > binomial_sf(8, 20, 0.1)


# --- evaluate_dish: verdict scenarios ---


def _measurements(dl, q10, q50, hours, obstruction) -> dict[str, Any]:
    return dict(
        dl_observed=dl,
        dl_q10=q10,
        dl_q50=q50,
        hours_of_day=hours,
        obstruction_pcts=obstruction,
        basis="region",
    )


def test_insufficient_data_below_minimum():
    n = MIN_MEASUREMENTS - 1
    v = evaluate_dish(
        **_measurements(
            dl=[100.0] * n,
            q10=[50.0] * n,
            q50=[120.0] * n,
            hours=list(range(n)),
            obstruction=[0.0] * n,
        )
    )
    assert v.verdict == "insufficient_data"
    assert v.n_evaluated == n
    assert v.p_value is None


def test_healthy_dish_not_flagged():
    # 30 measurements comfortably above q10 -> ~0 below-q10 hits -> healthy.
    n = 30
    v = evaluate_dish(
        **_measurements(
            dl=[100.0] * n,
            q10=[50.0] * n,
            q50=[110.0] * n,
            hours=[i % 24 for i in range(n)],
            obstruction=[1.0] * n,
        )
    )
    assert v.verdict == "healthy"
    assert v.below_q10_count == 0
    assert v.p_value is not None


def test_degraded_dish_flagged_across_many_hours():
    # 40 measurements, all below q10, spread across many hours -> underperforming.
    n = 40
    v = evaluate_dish(
        **_measurements(
            dl=[10.0] * n,
            q10=[50.0] * n,
            q50=[120.0] * n,
            hours=[i % 24 for i in range(n)],
            obstruction=[2.0] * n,
        )
    )
    assert v.verdict == "underperforming"
    assert v.below_q10_count == n
    assert v.distinct_hours_below >= MIN_DISTINCT_HOURS
    assert v.p_value is not None and v.p_value < ALPHA
    # median observed 10 vs expected median (q50) 120 -> ~92% below.
    assert v.effect_size_pct is not None and 90.0 < v.effect_size_pct < 93.0


def test_single_bad_hour_not_flagged():
    # 30 measurements: 12 below q10 but ALL in the same hour-of-day (one bad
    # evening). Binomial may reject, but the >=3-distinct-hours guard must not.
    n = 30
    dl = [100.0] * n
    q10 = [50.0] * n
    hours = [3] * n  # everything at 03:00 local
    for i in range(12):
        dl[i] = 10.0  # below q10, all same hour
    v = evaluate_dish(
        **_measurements(
            dl=dl,
            q10=q10,
            q50=[110.0] * n,
            hours=hours,
            obstruction=[1.0] * n,
        )
    )
    assert v.distinct_hours_below == 1
    assert v.verdict != "underperforming"


def test_obstruction_reported_first_as_evidence():
    n = 25
    v = evaluate_dish(
        **_measurements(
            dl=[100.0] * n,
            q10=[50.0] * n,
            q50=[110.0] * n,
            hours=[i % 24 for i in range(n)],
            obstruction=[7.0] * n,
        )
    )
    # Median obstruction is surfaced so the UI can lead with it (F9).
    assert v.median_obstruction_pct == 7.0


def test_window_capped_at_50_most_recent_is_caller_contract():
    # evaluate_dish evaluates exactly what it is given; the route windows to 50.
    n = 50
    v = evaluate_dish(
        **_measurements(
            dl=[100.0] * n,
            q10=[50.0] * n,
            q50=[110.0] * n,
            hours=[i % 24 for i in range(n)],
            obstruction=[0.0] * n,
        )
    )
    assert v.n_evaluated == 50
