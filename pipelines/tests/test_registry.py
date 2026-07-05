"""Active-cell registry (CLAUDE.md §5.3, §6.3).

The active set — cells with any label source or any user — is what drives the
orbital/weather precompute, so we never materialize a global dense grid. It is the
union of the H3 cells appearing across all mart sources.
"""

from orbitcast_pipelines.registry import active_cells


def test_active_cells_is_the_union_across_sources() -> None:
    atlas = [{"h3_cell": 1, "rtt_ms_median": 30.0}, {"h3_cell": 2, "rtt_ms_median": 40.0}]
    ookla = [{"h3_cell": 2, "tests": 10}, {"h3_cell": 3, "tests": 5}]
    assert active_cells(atlas, ookla) == {1, 2, 3}


def test_active_cells_empty_when_no_sources() -> None:
    assert active_cells() == set()
