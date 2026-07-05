"""Six LightGBM quantile boosters (CLAUDE.md D5, §6.2, §6.5).

{latency, dl_throughput} x q{0.1, 0.5, 0.9}. LightGBM is CPU-native and trains all
six in minutes at this data scale — no PyTorch, no MPS (D6). Inference is in-process
in the API (§7.1); artifacts are plain LightGBM text models plus a JSON manifest so
they load without pickle-version fragility.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
from numpy.typing import NDArray

from .features import FEATURE_COLUMNS

TARGETS: tuple[str, str] = ("latency", "dl_throughput")
QUANTILES: tuple[float, float, float] = (0.1, 0.5, 0.9)

# Small, CPU-friendly, deterministic. At <1e6 rows x ~15 features this is plenty;
# deterministic + single-thread keeps artifacts reproducible for the registry.
_DEFAULT_PARAMS: dict = {
    "objective": "quantile",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "min_data_in_leaf": 20,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 1,
    "seed": 0,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 1,
    "verbosity": -1,
}
_NUM_ROUNDS = 200

_MANIFEST = "manifest.json"


def _booster_file(target: str, quantile: float) -> str:
    # e.g. latency_q10.txt — quantile encoded as an integer percentage.
    return f"{target}_q{int(round(quantile * 100)):02d}.txt"


@dataclass
class ForecastModel:
    """The trained set of six quantile boosters plus the feature ordering they
    were trained on (guards against silent feature-vector drift, §6.2)."""

    boosters: dict[tuple[str, float], lgb.Booster]
    feature_names: list[str]
    # Per-target conformal offset (§6.4): the q10 edge shifts down and q90 up by
    # this amount so the band's empirical coverage hits the target. Empty = raw.
    calibration: dict[str, float] = field(default_factory=dict)

    @property
    def trained_targets(self) -> list[str]:
        """Targets that were actually trained (a target with no labels is skipped)."""
        return sorted({t for (t, _q) in self.boosters})

    def predict(self, x: NDArray[np.float64]) -> dict[str, dict[float, NDArray[np.float64]]]:
        x = np.asarray(x, dtype=float)
        out: dict[str, dict[float, NDArray[np.float64]]] = {}
        for target in self.trained_targets:
            preds = {
                q: np.asarray(self.boosters[(target, q)].predict(x), dtype=float) for q in QUANTILES
            }
            off = self.calibration.get(target, 0.0)
            if off:
                preds[0.1] = preds[0.1] - off
                preds[0.9] = preds[0.9] + off
            out[target] = preds
        return out

    def save(self, directory: Path | str) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for (target, q), booster in self.boosters.items():
            booster.save_model(str(directory / _booster_file(target, q)))
        (directory / _MANIFEST).write_text(
            json.dumps(
                {
                    "feature_names": self.feature_names,
                    "targets": self.trained_targets,
                    "quantiles": list(QUANTILES),
                    "calibration": self.calibration,
                },
                indent=2,
            )
        )
        return directory

    @classmethod
    def load(cls, directory: Path | str) -> ForecastModel:
        directory = Path(directory)
        manifest = json.loads((directory / _MANIFEST).read_text())
        boosters: dict[tuple[str, float], lgb.Booster] = {}
        for target in manifest["targets"]:
            for q in manifest["quantiles"]:
                path = directory / _booster_file(target, q)
                boosters[(target, q)] = lgb.Booster(model_file=str(path))
        return cls(
            boosters=boosters,
            feature_names=manifest["feature_names"],
            calibration=manifest.get("calibration", {}),
        )


def train_boosters(
    x: NDArray[np.float64],
    targets: Mapping[str, Sequence[float]],
    quantiles: Sequence[float] = QUANTILES,
    params: Mapping | None = None,
    num_rounds: int = _NUM_ROUNDS,
    sample_weights: Mapping[str, Sequence[float]] | None = None,
) -> ForecastModel:
    """Train one quantile booster per (target, quantile).

    ``x`` is a 2D array of shape (n, len(FEATURE_COLUMNS)); ``targets`` maps each
    target name to its label vector. ``sample_weights`` optionally down-weights
    noisier label sources per target (user > atlas > mlab, §6.4).
    """
    x = np.asarray(x, dtype=float)
    base = dict(_DEFAULT_PARAMS)
    if params:
        base.update(params)

    boosters: dict[tuple[str, float], lgb.Booster] = {}
    for target in targets:
        y = np.asarray(targets[target], dtype=float)
        weight = None
        if sample_weights is not None:
            weight = np.asarray(sample_weights[target], dtype=float)
        dataset = lgb.Dataset(x, label=y, weight=weight, feature_name=list(FEATURE_COLUMNS))
        for q in quantiles:
            booster_params = dict(base, alpha=q)
            boosters[(target, q)] = lgb.train(booster_params, dataset, num_boost_round=num_rounds)
    return ForecastModel(boosters=boosters, feature_names=list(FEATURE_COLUMNS))
