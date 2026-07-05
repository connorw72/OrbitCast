"""H3 spatial helpers (CLAUDE.md D1, §5.2).

H3 res-5 cells are the canonical geographic key everywhere, stored as BIGINT (the
64-bit cell id), never hex strings. The h3 library's geometry functions take the
hex string, so we convert at the boundary.
"""

import h3

RESOLUTION = 5


def cell_centroid(cell: int) -> tuple[float, float]:
    """Return the (lat, lon) centroid of an H3 cell given as a BIGINT id."""
    return h3.cell_to_latlng(h3.int_to_str(cell))
