"""Dish Doctor: per-user underperformance detection (CLAUDE.md §6.4).

Not an autoencoder. The promoted quantile model already yields bands, so
underperformance is a statistical test against them: under a healthy dish an
observation falls below the (cell, hour) q10 with probability 0.1. A one-sided
binomial test over the recent window flags a dish only when the drop is *sustained
across >= 3 distinct hours-of-day* (rules out one bad evening), and the verdict is
reported as interpretable evidence — effect size in user terms, obstruction first
(F9) — never as an accusation.

This module is pure: the serving route reads the user's measurements from Postgres
and runs the q10 inference, then hands the arrays here. That keeps the statistics
unit-testable without a database or a trained model.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from math import comb, isclose

from .fallback import Basis

# §6.4 thresholds.
MIN_MEASUREMENTS = 20  # below this, we say "keep contributing", not a verdict
WINDOW = 50  # the route evaluates at most this many most-recent rows
NULL_BELOW_Q10_PROB = 0.1  # P(below q10) under a healthy dish
ALPHA = 0.01  # one-sided binomial rejection level
MIN_DISTINCT_HOURS = 3  # sustained across >= this many hours-of-day

# Verdict labels surfaced to the client.
INSUFFICIENT_DATA = "insufficient_data"
HEALTHY = "healthy"
UNDERPERFORMING = "underperforming"


@dataclass(frozen=True)
class DishVerdict:
    """Interpretable Dish Doctor result (§6.4).

    ``effect_size_pct`` is how far the user's median download sits below the
    model's expected median (q50) for their conditions, as a percentage.
    ``median_obstruction_pct`` is surfaced first as a candidate explanation (F9).
    """

    verdict: str
    n_evaluated: int
    below_q10_count: int
    distinct_hours_below: int
    p_value: float | None
    effect_size_pct: float | None
    median_obstruction_pct: float | None
    basis: Basis


def binomial_sf(k: int, n: int, p: float) -> float:
    """One-sided upper tail P[X >= k] for X ~ Binomial(n, p).

    Exact (no scipy): sum of the pmf from k to n. Adding scipy for one tail would
    violate the project's dependency discipline (§6.5 ethos)."""
    if k <= 0:
        return 1.0
    return sum(comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(k, n + 1))


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    m = len(ordered)
    mid = m // 2
    if m % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def evaluate_dish(
    dl_observed: Sequence[float],
    dl_q10: Sequence[float],
    dl_q50: Sequence[float],
    hours_of_day: Sequence[int],
    obstruction_pcts: Sequence[float | None],
    basis: Basis,
) -> DishVerdict:
    """Resolve the §6.4 verdict for one user's download measurements.

    All sequences are aligned per measurement (already windowed to the most recent
    <= WINDOW rows by the caller). Below the minimum sample size we return
    ``insufficient_data`` rather than a fabricated verdict."""
    n = len(dl_observed)
    obs = [o for o in obstruction_pcts if o is not None]
    median_obstruction = _median(obs) if obs else None

    if n < MIN_MEASUREMENTS:
        return DishVerdict(
            verdict=INSUFFICIENT_DATA,
            n_evaluated=n,
            below_q10_count=0,
            distinct_hours_below=0,
            p_value=None,
            effect_size_pct=None,
            median_obstruction_pct=median_obstruction,
            basis=basis,
        )

    below_flags = [obs_dl < q10 for obs_dl, q10 in zip(dl_observed, dl_q10, strict=True)]
    below_count = sum(below_flags)
    distinct_hours_below = len(
        {h for h, is_below in zip(hours_of_day, below_flags, strict=True) if is_below}
    )
    p_value = binomial_sf(below_count, n, NULL_BELOW_Q10_PROB)

    flagged = p_value < ALPHA and distinct_hours_below >= MIN_DISTINCT_HOURS
    verdict = UNDERPERFORMING if flagged else HEALTHY

    effect_size_pct: float | None = None
    if flagged:
        expected = _median(dl_q50)
        observed = _median(dl_observed)
        if expected > 0 and not isclose(expected, 0.0):
            effect_size_pct = (expected - observed) / expected * 100.0

    return DishVerdict(
        verdict=verdict,
        n_evaluated=n,
        below_q10_count=below_count,
        distinct_hours_below=distinct_hours_below,
        p_value=p_value,
        effect_size_pct=effect_size_pct,
        median_obstruction_pct=median_obstruction,
        basis=basis,
    )
