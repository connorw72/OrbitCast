"""Mart validation gates (CLAUDE.md Phase 2 DoD).

The backfill asserts row counts and null-rate bounds on every mart it writes, so a
silently empty or malformed ingest fails loudly instead of poisoning training.
"""

import pytest
from orbitcast_pipelines.validate import assert_mart, null_rate


def test_null_rate_counts_missing_values() -> None:
    rows = [{"x": 1}, {"x": None}, {"x": 3}, {"x": None}]
    assert null_rate(rows, "x") == 0.5


def test_assert_mart_passes_within_bounds() -> None:
    rows = [{"h3_cell": 1, "v": 10}, {"h3_cell": 2, "v": 20}]
    assert assert_mart(rows, required=["h3_cell", "v"], min_rows=2) is True


def test_assert_mart_rejects_too_few_rows() -> None:
    with pytest.raises(ValueError, match="rows"):
        assert_mart([{"h3_cell": 1}], required=["h3_cell"], min_rows=5)


def test_assert_mart_rejects_excess_nulls() -> None:
    rows = [{"h3_cell": 1}, {"h3_cell": None}]
    with pytest.raises(ValueError, match="null"):
        assert_mart(rows, required=["h3_cell"], min_rows=1, max_null_rate=0.0)
