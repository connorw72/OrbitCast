"""Six LightGBM quantile boosters (CLAUDE.md D5, §6.2).

{latency, dl_throughput} x q{0.1, 0.5, 0.9}. These tests pin the structural
contract — six series out, quantiles ordered in the mean, the median actually
learns signal, and artifacts round-trip — rather than exact numbers, which depend
on LightGBM internals.
"""

import numpy as np
import pytest
from orbitcast_ml.features import FEATURE_COLUMNS
from orbitcast_ml.metrics import mae
from orbitcast_ml.models import QUANTILES, TARGETS, ForecastModel, train_boosters


@pytest.fixture(scope="module")
def synthetic():
    """A monotone signal in one feature plus noise, over the full feature width."""
    rng = np.random.default_rng(0)
    n = 1500
    width = len(FEATURE_COLUMNS)
    x = rng.standard_normal((n, width))
    signal = x[:, 0]  # first feature carries the signal
    latency = 40.0 + 15.0 * signal + rng.normal(0, 5, n)
    dl = 120.0 - 20.0 * signal + rng.normal(0, 8, n)
    return x, {"latency": latency, "dl_throughput": dl}


@pytest.fixture(scope="module")
def model(synthetic):
    """Train the six boosters once; the read-only tests share this instance."""
    x, targets = synthetic
    return train_boosters(x, targets)


def test_predict_returns_all_six_series(synthetic, model):
    x, _ = synthetic
    preds = model.predict(x[:10])
    assert set(preds) == set(TARGETS)
    for target in TARGETS:
        assert set(preds[target]) == set(QUANTILES)
        for q in QUANTILES:
            assert preds[target][q].shape == (10,)


def test_quantiles_are_ordered_in_the_mean(synthetic, model):
    x, _ = synthetic
    preds = model.predict(x)
    for target in TARGETS:
        q10 = preds[target][0.1].mean()
        q50 = preds[target][0.5].mean()
        q90 = preds[target][0.9].mean()
        assert q10 < q50 < q90


def test_median_beats_predicting_the_mean(synthetic, model):
    x, targets = synthetic
    preds = model.predict(x)
    for target, y in targets.items():
        q50_mae = mae(y, preds[target][0.5])
        baseline_mae = mae(y, np.full_like(y, y.mean()))
        assert q50_mae < baseline_mae


def test_artifacts_round_trip(synthetic, model, tmp_path):
    x, _ = synthetic
    before = model.predict(x[:20])
    model.save(tmp_path)
    reloaded = ForecastModel.load(tmp_path)
    after = reloaded.predict(x[:20])
    assert reloaded.feature_names == FEATURE_COLUMNS
    for target in TARGETS:
        for q in QUANTILES:
            np.testing.assert_allclose(before[target][q], after[target][q])
