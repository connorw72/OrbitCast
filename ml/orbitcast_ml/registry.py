"""Model registry + end-to-end train runner (CLAUDE.md §5.5, §6.4, Phase 3).

`run_training` is the body of the weekly `train_models` Dagster job: build a
version, train + evaluate, always emit an eval report (checked into docs/evals/),
and promote — writing artifacts and advancing the PROMOTED pointer — only when the
gate passes (§6.4). The serving path resolves the live model through PROMOTED, so a
failed run leaves the previous model in place.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .train import stratified_time_split, time_split, train_and_evaluate

# Pointer file (under the models root) naming the currently promoted version.
PROMOTED_POINTER = "PROMOTED"


def new_version(now: datetime | None = None) -> str:
    """A lexicographically-sortable UTC version string, e.g. v20260705T010203Z."""
    now = now or datetime.now(UTC)
    return "v" + now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def write_eval_report(report: dict, evals_dir: Path | str, version: str) -> Path:
    evals_dir = Path(evals_dir)
    evals_dir.mkdir(parents=True, exist_ok=True)
    path = evals_dir / f"{version}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    return path


def run_training(
    rows: Sequence[dict],
    cutoff: datetime | None,
    models_root: Path | str,
    evals_dir: Path | str,
    num_rounds: int | None = None,
) -> dict:
    """Train, evaluate against the gate, and persist artifacts + eval report.

    ``cutoff=None`` splits each (target, source) at its own tail
    (`stratified_time_split`) — the default for heterogeneous-source data, where one
    global cutoff can starve a target of test rows or make calibration and test
    different sources. Pass an explicit ``cutoff`` to force a single global split.

    Returns the eval report (with ``version`` and ``promoted``). Model artifacts
    and the PROMOTED pointer are written only when ``promoted`` is True.
    """
    models_root = Path(models_root)
    if cutoff is None:
        train_rows, test_rows = stratified_time_split(rows)
    else:
        train_rows, test_rows = time_split(rows, cutoff)
    model, report = train_and_evaluate(train_rows, test_rows, history=rows, num_rounds=num_rounds)

    version = new_version()
    report["version"] = version
    write_eval_report(report, evals_dir, version)

    if report["promoted"]:
        model.save(models_root / version)
        models_root.mkdir(parents=True, exist_ok=True)
        (models_root / PROMOTED_POINTER).write_text(version)

    return report
