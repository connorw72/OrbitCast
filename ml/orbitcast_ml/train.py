"""Train orchestration + evaluation (CLAUDE.md §6.4, Phase 3).

Time-based split, a persistence baseline (same cell, same hour last week), and the
promotion gate. `train_and_evaluate` ties the training-matrix rows to the six
boosters and produces the eval report that gates promotion.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from .metrics import COVERAGE_BOUNDS, coverage, mae, pinball_loss, promotion_decision
from .models import QUANTILES, TARGETS, ForecastModel, train_boosters
from .training_matrix import to_arrays

PERSISTENCE_LAG_DAYS = 7


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

    train_targets = {t: to_arrays(train_rows, t)[1] for t in available}
    weights = {t: to_arrays(train_rows, t)[2] for t in available}
    x_train = to_arrays(train_rows, available[0])[0]

    kwargs = {"num_rounds": num_rounds} if num_rounds is not None else {}
    model = _train_with_weights(x_train, train_targets, weights, **kwargs)

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


def _train_with_weights(x_train, train_targets, weights, **kwargs) -> ForecastModel:
    # train_boosters builds its own Dataset per target; pass weights through so
    # noisier sources (mlab) count for less than user/atlas labels (§6.4).
    return train_boosters(x_train, train_targets, sample_weights=weights, **kwargs)
