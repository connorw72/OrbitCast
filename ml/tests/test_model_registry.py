"""Model registry + end-to-end train runner (CLAUDE.md §6.4, Phase 3 DoD).

`run_training` turns training-matrix rows into a promotion decision: it always
writes an eval report (checked into docs/evals/), and writes model artifacts +
advances the PROMOTED pointer only when the gate passes. The serving path reads
the PROMOTED pointer, so artifacts-exist must equal promoted.
"""

import json
from datetime import UTC, datetime, timedelta

import numpy as np
from orbitcast_ml.features import FEATURE_COLUMNS
from orbitcast_ml.models import ForecastModel
from orbitcast_ml.registry import PROMOTED_POINTER, new_version, run_training


def _fabricate_rows():
    """A learnable matrix: label driven by cell_lat with a 7-day-persistable signal."""
    rng = np.random.default_rng(0)
    rows = []
    base = datetime(2026, 6, 1, 0, tzinfo=UTC)
    for cell in (1, 2, 3):
        for hour in range(0, 24 * 40):  # 40 days hourly
            ts = base + timedelta(hours=hour)
            for target, scale in (("latency", 1.0), ("dl_throughput", 2.0)):
                feats = {c: 0.0 for c in FEATURE_COLUMNS}
                feats["cell_lat"] = float(cell) * 10.0
                feats["hour_sin"] = np.sin(2 * np.pi * (hour % 24) / 24)
                label = 30.0 * scale + 5.0 * cell + rng.normal(0, 1.0)
                rows.append(
                    {
                        "h3_cell": cell,
                        "hour_utc": ts.replace(tzinfo=None),
                        "target": target,
                        "label": label,
                        "source_quality": 2.0,
                        **feats,
                    }
                )
    return rows


def test_new_version_is_sortable_and_unique():
    v1 = new_version(datetime(2026, 7, 5, 1, 2, 3, tzinfo=UTC))
    v2 = new_version(datetime(2026, 7, 5, 1, 2, 4, tzinfo=UTC))
    assert v1 < v2


def test_run_training_writes_eval_report_and_returns_structure(tmp_path):
    rows = _fabricate_rows()
    cutoff = datetime(2026, 7, 8, tzinfo=UTC)  # last ~few days are the test month
    models_root = tmp_path / "models"
    evals_dir = tmp_path / "evals"
    report = run_training(rows, cutoff, models_root, evals_dir, num_rounds=60)

    assert set(report["targets"]) == {"latency", "dl_throughput"}
    assert isinstance(report["promoted"], bool)
    # Eval report is always written for the run.
    eval_file = evals_dir / f"{report['version']}.json"
    assert eval_file.exists()
    assert json.loads(eval_file.read_text())["version"] == report["version"]


def test_artifacts_written_iff_promoted(tmp_path):
    rows = _fabricate_rows()
    cutoff = datetime(2026, 7, 8, tzinfo=UTC)
    models_root = tmp_path / "models"
    evals_dir = tmp_path / "evals"
    report = run_training(rows, cutoff, models_root, evals_dir, num_rounds=60)

    version_dir = models_root / report["version"]
    pointer = models_root / PROMOTED_POINTER
    if report["promoted"]:
        assert version_dir.exists()
        assert pointer.read_text().strip() == report["version"]
        # The promoted artifacts load back into a usable model.
        model = ForecastModel.load(version_dir)
        assert model.feature_names == FEATURE_COLUMNS
    else:
        assert not version_dir.exists()
        assert not pointer.exists()
