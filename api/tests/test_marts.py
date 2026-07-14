"""Mart access memoization (design doc Part 1c).

`resolve_ookla` / `resolve_median` must not re-read + linearly re-scan whole
Parquet marts on every request: parsed contents are memoized by (path, mtime) —
the same posture as `satellites.load_satellites` — and looked up by h3_cell.
A rewritten mart (new mtime) must invalidate the memo; a mart that appears
after a miss must be picked up.
"""

import math
import os
from datetime import UTC, datetime
from pathlib import Path

import h3
import pyarrow as pa
import pyarrow.parquet as pq
from orbitcast_api import forecast as fc

_CELL = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 5))


def _write_ookla(marts_dir: Path, baseline: float, mtime: float) -> None:
    path = marts_dir / "ookla_context.parquet"
    table = pa.table(
        {"h3_cell": [_CELL], "terrestrial_baseline_mbps": [baseline], "devices": [40.0]}
    )
    pq.write_table(table, str(path))
    os.utime(path, (mtime, mtime))


def _write_stats(marts_dir: Path, median: float, mtime: float) -> None:
    path = marts_dir / "cell_label_stats.parquet"
    table = pa.table({"h3_cell": [_CELL], "median": [median], "hours": [500]})
    pq.write_table(table, str(path))
    os.utime(path, (mtime, mtime))


def _count_parquet_reads(monkeypatch):
    reads: list[Path] = []
    real = fc._read_parquet_rows

    def counting(path: Path) -> list[dict]:
        reads.append(path)
        return real(path)

    monkeypatch.setattr(fc, "_read_parquet_rows", counting)
    return reads


def test_resolve_ookla_reads_each_mart_version_once(tmp_path, monkeypatch):
    reads = _count_parquet_reads(monkeypatch)
    base = datetime(2026, 7, 13, tzinfo=UTC).timestamp()
    _write_ookla(tmp_path, baseline=80.0, mtime=base)

    assert fc.resolve_ookla(_CELL, tmp_path) == (80.0, 40.0)
    assert fc.resolve_ookla(_CELL, tmp_path) == (80.0, 40.0)
    assert len(reads) == 1

    # Rewritten mart (new mtime) invalidates the memo.
    _write_ookla(tmp_path, baseline=95.0, mtime=base + 60)
    assert fc.resolve_ookla(_CELL, tmp_path) == (95.0, 40.0)
    assert len(reads) == 2


def test_resolve_ookla_missing_cell_and_missing_mart(tmp_path):
    baseline, devices = fc.resolve_ookla(_CELL, tmp_path)  # no mart at all
    assert math.isnan(baseline) and math.isnan(devices)

    _write_ookla(tmp_path, baseline=80.0, mtime=datetime(2026, 7, 13, tzinfo=UTC).timestamp())
    other = h3.str_to_int(h3.latlng_to_cell(-33.9, 151.2, 5))
    baseline, devices = fc.resolve_ookla(other, tmp_path)
    assert math.isnan(baseline) and math.isnan(devices)


def test_mart_appearing_after_a_miss_is_picked_up(tmp_path):
    baseline, _ = fc.resolve_ookla(_CELL, tmp_path)
    assert math.isnan(baseline)
    _write_ookla(tmp_path, baseline=80.0, mtime=datetime(2026, 7, 13, tzinfo=UTC).timestamp())
    assert fc.resolve_ookla(_CELL, tmp_path) == (80.0, 40.0)


def test_resolve_median_memoizes_and_still_resolves_basis(tmp_path, monkeypatch):
    reads = _count_parquet_reads(monkeypatch)
    base = datetime(2026, 7, 13, tzinfo=UTC).timestamp()
    _write_stats(tmp_path, median=25.0, mtime=base)

    assert fc.resolve_median(_CELL, tmp_path) == (25.0, "cell")
    assert fc.resolve_median(_CELL, tmp_path) == (25.0, "cell")
    # One read for the stats mart, at most one probe of the (absent) priors mart.
    assert len([p for p in reads if p.name == "cell_label_stats.parquet"]) == 1

    _write_stats(tmp_path, median=30.0, mtime=base + 60)
    assert fc.resolve_median(_CELL, tmp_path) == (30.0, "cell")
