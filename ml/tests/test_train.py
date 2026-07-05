"""Train orchestration + eval report (CLAUDE.md §6.4, Phase 3).

Time-based split (train <= cutoff, test after), a persistence baseline (same cell,
same hour last week), and the promotion gate. The gate is what keeps a broken
model out of production, so its inputs are pinned here.
"""

import math
from datetime import UTC, datetime

from orbitcast_ml.metrics import COVERAGE_BOUNDS
from orbitcast_ml.train import evaluate_predictions, time_split


def _row(cell, hour, target, label):
    return {"h3_cell": cell, "hour_utc": hour, "target": target, "label": label}


def test_time_split_partitions_at_cutoff():
    rows = [_row(1, datetime(2026, 7, d, 12), "latency", float(d)) for d in range(1, 11)]
    cutoff = datetime(2026, 7, 6, tzinfo=UTC)
    train, test = time_split(rows, cutoff)
    assert [r["label"] for r in train] == [1, 2, 3, 4, 5]
    assert [r["label"] for r in test] == [6, 7, 8, 9, 10]


def test_evaluate_reports_persistence_and_gate():
    # One cell, latency. day0 and day7 (7 days apart) so persistence has a prior.
    c = 42
    d0 = datetime(2026, 7, 1, 12)
    d7 = datetime(2026, 7, 8, 12)
    history = [_row(c, d0, "latency", 30.0), _row(c, d7, "latency", 20.0)]
    test_rows = [_row(c, d7, "latency", 20.0)]
    # Model median nails 20 exactly; persistence predicts day0's 30 -> error 10.
    preds = {"latency": {0.1: [15.0], 0.5: [20.0], 0.9: [25.0]}}
    report = evaluate_predictions(test_rows, preds, history)
    lat = report["latency"]
    assert math.isclose(lat["persistence_mae"], 10.0)
    assert math.isclose(lat["q50_mae"], 0.0)
    assert lat["beats_persistence"] is True
    # Single point inside [15,25] -> coverage 1.0, outside the calibration band.
    assert math.isclose(lat["coverage"], 1.0)
    assert lat["calibrated"] is False
    assert lat["promote"] is False


def test_promote_true_when_calibrated_and_beats_persistence():
    c = 7
    week = datetime(2026, 7, 8, 12)
    # 5 test points; band covers 4/5 = 0.80 (in [0.78, 0.82]); q50 beats persistence.
    labels = [10.0, 20.0, 30.0, 40.0, 100.0]
    hours = [week.replace(day=8 + i) for i in range(5)]
    priors = [week.replace(day=1 + i) for i in range(5)]
    history = [_row(c, priors[i], "latency", labels[i] + 12.0) for i in range(5)]
    history += [_row(c, hours[i], "latency", labels[i]) for i in range(5)]
    test_rows = [_row(c, hours[i], "latency", labels[i]) for i in range(5)]
    q10 = [x - 5 for x in labels]
    q50 = [x + 1 for x in labels]  # small error, well under persistence's 12
    q90 = [x + 5 for x in labels]
    q90[4] = 90.0  # push the 5th point (100) outside the band -> 4/5 coverage
    preds = {"latency": {0.1: q10, 0.5: q50, 0.9: q90}}
    report = evaluate_predictions(test_rows, preds, history)
    lat = report["latency"]
    lo, hi = COVERAGE_BOUNDS
    assert lo <= lat["coverage"] <= hi
    assert lat["beats_persistence"] is True
    assert lat["promote"] is True
