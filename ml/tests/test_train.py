"""Train orchestration + eval report (CLAUDE.md §6.4, Phase 3).

Time-based split (train <= cutoff, test after), a persistence baseline (same cell,
same hour last week), and the promotion gate. The gate is what keeps a broken
model out of production, so its inputs are pinned here.
"""

import math
from datetime import UTC, datetime, timedelta

import numpy as np
from orbitcast_ml.features import FEATURE_COLUMNS
from orbitcast_ml.metrics import COVERAGE_BOUNDS, coverage
from orbitcast_ml.models import ForecastModel, train_boosters
from orbitcast_ml.train import (
    evaluate_predictions,
    fit_calibration,
    stratified_time_split,
    time_split,
    train_and_evaluate,
)
from orbitcast_ml.training_matrix import to_arrays


def _row(cell, hour, target, label):
    return {"h3_cell": cell, "hour_utc": hour, "target": target, "label": label}


def _matrix_rows(n, rng, start=datetime(2026, 6, 1)):
    """Full training-matrix rows with a learnable latency signal in one feature."""
    rows = []
    for i in range(n):
        feats = {c: float(rng.standard_normal()) for c in FEATURE_COLUMNS}
        label = 40.0 + 15.0 * feats["hour_sin"] + rng.normal(0, 8)
        rows.append(
            {
                "h3_cell": 1,
                "hour_utc": start + timedelta(hours=i),
                "target": "latency",
                "label": label,
                "source_quality": 1.0,
                **feats,
            }
        )
    return rows


def test_fit_calibration_reaches_target_coverage_on_calibration_set():
    rng = np.random.default_rng(1)
    fit_rows = _matrix_rows(600, rng)
    calib_rows = _matrix_rows(300, rng, start=datetime(2026, 6, 26))
    x, y, _w = to_arrays(fit_rows, "latency")
    model = train_boosters(x, {"latency": y})

    offsets = fit_calibration(model, calib_rows, target_coverage=0.8)
    assert "latency" in offsets

    calibrated = ForecastModel(model.boosters, model.feature_names, calibration=offsets)
    cx = to_arrays(calib_rows, "latency")[0]
    preds = calibrated.predict(cx)
    cov = coverage(
        [r["label"] for r in calib_rows], preds["latency"][0.1], preds["latency"][0.9]
    )
    assert cov >= 0.8


def test_train_and_evaluate_calibrates_the_trained_target():
    rng = np.random.default_rng(2)
    rows = _matrix_rows(500, rng)
    train_rows, test_rows = time_split(rows, rows[400]["hour_utc"])
    model, _report = train_and_evaluate(train_rows, test_rows, history=rows)
    assert "latency" in model.calibration
    assert math.isfinite(model.calibration["latency"])


def _target_rows(hours, rng, target, start=datetime(2026, 6, 1)):
    rows = []
    for i in hours:
        feats = {c: float(rng.standard_normal()) for c in FEATURE_COLUMNS}
        label = 40.0 + 15.0 * feats["hour_sin"] + rng.normal(0, 8)
        rows.append(
            {
                "h3_cell": 1,
                "hour_utc": start + timedelta(hours=i),
                "target": target,
                "label": label,
                "source_quality": 1.0,
                **feats,
            }
        )
    return rows


def test_train_and_evaluate_handles_targets_with_unequal_row_counts():
    # Regression: M-Lab yields latency and dl_throughput as separate long-format
    # rows with different null patterns, so each target has a distinct feature
    # matrix. The trainer must use each target's own X, not target[0]'s, else
    # LightGBM raises "Length of labels differs from the length of #data".
    # Both targets span the same range (as real M-Lab does) but throughput is
    # sparser, so their row counts differ.
    rng = np.random.default_rng(7)
    rows = _target_rows(range(500), rng, "latency") + _target_rows(
        range(0, 500, 2), rng, "dl_throughput"
    )
    cutoff = datetime(2026, 6, 1) + timedelta(hours=450)
    train_rows, test_rows = time_split(rows, cutoff)

    model, report = train_and_evaluate(train_rows, test_rows, history=rows)

    assert set(report["targets"]) == {"latency", "dl_throughput"}
    for target in ("latency", "dl_throughput"):
        x = to_arrays(test_rows, target)[0]
        preds = model.predict(x)[target]
        assert len(preds[0.5]) == len(x)


def test_split_gives_every_target_a_test_set_across_disjoint_ranges():
    # Real M-Lab situation: throughput (M-Lab) and latency (Atlas) cover disjoint
    # time ranges. A single global cutoff falls in the gap and hands throughput
    # zero test rows; stratified splitting must hold out each group's own tail.
    rng = np.random.default_rng(11)
    latency = _target_rows(range(200), rng, "latency", start=datetime(2026, 6, 27))
    throughput = _target_rows(range(200), rng, "dl_throughput", start=datetime(2026, 5, 31))
    train, test = stratified_time_split(latency + throughput)

    test_targets = {r["target"] for r in test}
    assert test_targets == {"latency", "dl_throughput"}
    # Every test row is strictly later than that target's train rows.
    for target in ("latency", "dl_throughput"):
        tr = [r["hour_utc"] for r in train if r["target"] == target]
        te = [r["hour_utc"] for r in test if r["target"] == target]
        assert te and min(te) >= max(tr)


def test_split_stratifies_by_source_so_calibration_and_test_share_the_mix():
    # One target fed by two sources with disjoint time ranges (Atlas latency is
    # recent; M-Lab minRTT is earlier). A per-target-only split would put one
    # source entirely in train and the other in test, making calibration and test
    # non-exchangeable and breaking conformal coverage (§6.4). Stratifying by
    # (target, source) must place BOTH sources in the test tail.
    rng = np.random.default_rng(13)
    atlas = _target_rows(range(200), rng, "latency", start=datetime(2026, 6, 27))
    for r in atlas:
        r["source"] = "atlas"
    mlab = _target_rows(range(200), rng, "latency", start=datetime(2026, 5, 31))
    for r in mlab:
        r["source"] = "mlab"

    _train, test = stratified_time_split(atlas + mlab)
    assert {r["source"] for r in test} == {"atlas", "mlab"}


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
