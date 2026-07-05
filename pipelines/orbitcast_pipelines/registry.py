"""Active-cell registry (CLAUDE.md §5.3, §6.3).

The active set is the union of H3 cells that have any label source or any user.
It drives the orbital/weather precompute so the feature store stays sparse.
"""

from collections.abc import Iterable


def active_cells(*sources: Iterable[dict]) -> set[int]:
    """Union of `h3_cell` values across any number of mart row sources."""
    cells: set[int] = set()
    for source in sources:
        cells.update(row["h3_cell"] for row in source)
    return cells
