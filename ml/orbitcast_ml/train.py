"""Train orchestration + evaluation (CLAUDE.md §6.4, Phase 3).

Time-based split, a persistence baseline (same cell, same hour last week), and the
promotion gate. `train_and_evaluate` ties the training-matrix rows to the six
boosters and produces the eval report that gates promotion.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from .metrics import (
    COVERAGE_BOUNDS,
    conformal_offset,
    coverage,
    mae,
    pinball_loss,
    promotion_decision,
)
from .models import QUANTILES, TARGETS, ForecastModel, train_boosters
from .training_matrix import to_arrays

PERSISTENCE_LAG_DAYS = 7
# Marginal coverage the conformal recalibration targets — the center of the
# promotion gate's [0.78, 0.82] band (§6.4).
CALIBRATION_COVERAGE = 0.8


def _naive_utc(dt: datetime) -> datetime:
    """Normalize to naive UTC so warehouse-naive and caller-aware datetimes compare."""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def time_split(rows: Sequence[dict], cutoff: datetime) -> tuple[list[dict], list[dict]]:
    """Split matrix rows into (train <= cutoff, test after cutoff) by hour_utc."""
    c = _naive_utc(cutoff)
    train = [r for r in rows if _naive_utc(r["hour_utc"]) < c]
    test = [r for r in rows if _naive_utc(r["hour_utc"]) >= c]
    return train, test


def adaptive_cutoff(rows: Sequence[dict], test_fraction: float = 0.25) -> datetime | None:
    """Cutoff that holds out the most recent ``test_fraction`` of distinct hours.

    A fixed calendar window (e.g. last 30 days) can't split a short bootstrap
    history — all rows land on one side. This picks the cutoff from the data's own
    time range so train (past) and test (future) are both non-empty, honoring the
    time-based-split principle (§6.4) at any history length. Returns None when there
    are fewer than two distinct hours (nothing to split).
    """
    hours = sorted({_naive_utc(r["hour_utc"]) for r in rows})
    if len(hours) < 2:
        return None
    idx = max(1, min(len(hours) - 1, round(len(hours) * (1 - test_fraction))))
    return hours[idx]


def stratified_time_split(
    rows: Sequence[dict], test_fraction: float = 0.25
) -> tuple[list[dict], list[dict]]:
    """Split each (target, source) group at its own time cutoff, then union.

    Sources cover different time ranges *and* different distributions (Atlas
    latency is recent and low-variance; M-Lab minRTT is earlier and wider). A
    single global cutoff then (a) hands a whole target zero test rows when its
    sources don't overlap, and (b) makes the calibration set one source while the
    test set is another — breaking the exchangeability the conformal band relies
    on (§6.4), which shows up as coverage well below target. Splitting per
    (target, source) holds out each source's own recent tail, so train, calibration
    and test all carry the same source mix. A group with fewer than two distinct
    hours goes wholly to train (nothing to hold out).
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["target"], r.get("source"))].append(r)

    train: list[dict] = []
    test: list[dict] = []
    for group_rows in groups.values():
        cutoff = adaptive_cutoff(group_rows, test_fraction)
        if cutoff is None:
            train.extend(group_rows)
            continue
        tr, te = time_split(group_rows, cutoff)
        train.extend(tr)
        test.extend(te)
    return train, test


def fit_calibration(
    model: ForecastModel,
    calib_rows: Sequence[dict],
    target_coverage: float = CALIBRATION_COVERAGE,
) -> dict[str, float]:
    """Per-target conformal offset from a held-out calibration set (§6.4, CQR).

    Reads the model's *raw* (uncalibrated) q10/q90 boosters directly, so the offset
    is computed independent of any calibration already on ``model``. A target with
    no calibration labels is skipped.
    """
    offsets: dict[str, float] = {}
    for target in model.trained_targets:
        x, y, _w = to_arrays(calib_rows, target)
        if y.size == 0:
            continue
        q10 = model.boosters[(target, 0.1)].predict(x)
        q90 = model.boosters[(target, 0.9)].predict(x)
        offsets[target] = conformal_offset(y, q10, q90, target_coverage)
    return offsets


def _calibration_split(rows: Sequence[dict]) -> tuple[list[dict], list[dict]]:
    """Hold out the most recent slice of the training rows for conformal calibration.

    Keeps the split time-based (calibration is the future relative to the fit set),
    consistent with §6.4, and stratified by (target, source) so the calibration set
    carries the same source mix as the test set — a single global cutoff would make
    calibration one source and test another, breaking conformal coverage. Returns
    ``(rows, [])`` when there isn't enough history to carve out a calibration set,
    so training still proceeds uncalibrated.
    """
    fit_rows, calib_rows = stratified_time_split(rows)
    if not calib_rows:
        return list(rows), []
    return fit_rows, calib_rows


def _history_map(rows: Sequence[dict]) -> dict[tuple[int, str, datetime], float]:
    return {(r["h3_cell"], r["target"], _naive_utc(r["hour_utc"])): r["label"] for r in rows}


def evaluate_predictions(
    test_rows: Sequence[dict],
    preds: Mapping[str, Mapping[float, Sequence[float]]],
    history: Sequence[dict],
    lag_days: int = PERSISTENCE_LAG_DAYS,
) -> dict[str, dict]:
    """Score quantile predictions per target and apply the promotion gate.

    ``preds[target][q]`` aligns positionally with ``[r for r in test_rows if
    r['target']==target]``. Skill (q50 MAE vs persistence) is compared only on
    rows that have a same-cell label ``lag_days`` earlier — a fair baseline set.
    """
    hist = _history_map(history)
    lag = timedelta(days=lag_days)
    report: dict[str, dict] = {}
    for target, qd in preds.items():
        rows_t = [r for r in test_rows if r["target"] == target]
        y = [r["label"] for r in rows_t]
        q10 = list(qd[0.1])
        q50 = list(qd[0.5])
        q90 = list(qd[0.9])

        cov = coverage(y, q10, q90)
        pinball = {q: pinball_loss(y, list(qd[q]), q) for q in qd}

        fair_y: list[float] = []
        fair_q50: list[float] = []
        fair_persist: list[float] = []
        for i, r in enumerate(rows_t):
            key = (r["h3_cell"], target, _naive_utc(r["hour_utc"]) - lag)
            prior = hist.get(key)
            if prior is None:
                continue
            fair_y.append(r["label"])
            fair_q50.append(q50[i])
            fair_persist.append(prior)

        if fair_y:
            q50_mae = mae(fair_y, fair_q50)
            persistence_mae = mae(fair_y, fair_persist)
        else:
            q50_mae = float("inf")
            persistence_mae = float("inf")

        lo, hi = COVERAGE_BOUNDS
        report[target] = {
            "pinball": pinball,
            "coverage": cov,
            "q50_mae": q50_mae,
            "persistence_mae": persistence_mae,
            "beats_persistence": q50_mae < persistence_mae,
            "calibrated": lo <= cov <= hi,
            "promote": promotion_decision(cov, q50_mae, persistence_mae),
        }
    return report


def train_and_evaluate(
    train_rows: Sequence[dict],
    test_rows: Sequence[dict],
    history: Sequence[dict] | None = None,
    num_rounds: int | None = None,
) -> tuple[ForecastModel, dict]:
    """Train the six boosters on ``train_rows`` and evaluate on ``test_rows``.

    ``history`` supplies the persistence baseline's 7-day-prior labels (defaults to
    train+test). Returns the model and a report whose per-target ``promote`` flags
    are aggregated into a top-level ``promoted``.
    """
    if history is None:
        history = [*train_rows, *test_rows]

    # Train only targets that actually have labels (M-Lab throughput is deferred, so
    # early on only latency has data — training its booster alone must still work).
    available = [t for t in TARGETS if to_arrays(train_rows, t)[1].size > 0]
    if not available:
        raise ValueError("no target has training labels")

    kwargs = {"num_rounds": num_rounds} if num_rounds is not None else {}

    # The deployed model uses ALL of train; the test set stays a pristine final eval.
    model = _boosters_on(train_rows, available, **kwargs)

    # Conformal recalibration: hold out train's most recent slice, fit a proxy on the
    # earlier part, and estimate the q10/q90 offset from the proxy's residuals on that
    # slice (§6.4, CQR). The proxy sees less data than the deployed model, so its
    # residuals — and thus the offset — are conservative (coverage >= target).
    _fit_rows, calib_rows = _calibration_split(train_rows)
    if calib_rows and _fit_rows:
        proxy = _boosters_on(_fit_rows, available, **kwargs)
        model.calibration = fit_calibration(proxy, calib_rows)

    preds: dict[str, dict[float, list[float]]] = {}
    for target in available:
        x_test = to_arrays(test_rows, target)[0]
        target_pred = model.predict(x_test)[target]
        preds[target] = {q: list(target_pred[q]) for q in QUANTILES}

    per_target = evaluate_predictions(test_rows, preds, history)
    report = {
        "trained_at": datetime.now(UTC).isoformat(),
        "n_train": len(train_rows),
        "n_test": len(test_rows),
        "targets": per_target,
        "promoted": bool(per_target) and all(t["promote"] for t in per_target.values()),
    }
    return model, report


def _boosters_on(rows: Sequence[dict], available: Sequence[str], **kwargs) -> ForecastModel:
    """Train the quantile boosters for ``available`` targets from matrix ``rows``.

    Passes per-target sample weights through so noisier sources (mlab) count for
    less than user/atlas labels (§6.4).
    """
    # Each target is a distinct subset of long-format rows (differing null
    # patterns), so every target needs its own feature matrix — not target[0]'s,
    # which would mismatch label length and crash LightGBM.
    arrays = {t: to_arrays(rows, t) for t in available}
    feature_matrices = {t: a[0] for t, a in arrays.items()}
    train_targets = {t: a[1] for t, a in arrays.items()}
    weights = {t: a[2] for t, a in arrays.items()}
    x_train = feature_matrices[available[0]]
    return train_boosters(
        x_train,
        train_targets,
        sample_weights=weights,
        feature_matrices=feature_matrices,
        **kwargs,
    )
