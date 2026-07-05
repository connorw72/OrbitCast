"""Mart validation gates (CLAUDE.md Phase 2 DoD)."""

from collections.abc import Sequence


def null_rate(rows: Sequence[dict], column: str) -> float:
    if not rows:
        return 0.0
    missing = sum(1 for r in rows if r.get(column) is None)
    return missing / len(rows)


def assert_mart(
    rows: Sequence[dict],
    required: Sequence[str],
    min_rows: int,
    max_null_rate: float = 0.0,
) -> bool:
    """Fail loudly if a mart has too few rows or too many nulls in key columns."""
    if len(rows) < min_rows:
        raise ValueError(f"mart has {len(rows)} rows, expected >= {min_rows}")
    for column in required:
        rate = null_rate(rows, column)
        if rate > max_null_rate:
            raise ValueError(f"column {column!r} null rate {rate:.3f} exceeds {max_null_rate}")
    return True
