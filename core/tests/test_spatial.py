"""H3 spatial helpers (CLAUDE.md D1, §5.2).

H3 cells are the canonical geographic key, stored as BIGINT (the 64-bit cell id).
The server converts a cell to its centroid lat/lon; it never receives raw
coordinates (D12).
"""

import h3
from orbitcast_core.spatial import cell_centroid


def test_centroid_roundtrips_within_the_cell() -> None:
    # A known Seattle-area res-5 cell; centroid must land back inside it.
    lat, lon, res = 47.6062, -122.3321, 5
    cell_int = h3.str_to_int(h3.latlng_to_cell(lat, lon, res))

    clat, clon = cell_centroid(cell_int)

    # Round-trips: the centroid resolves back to the same cell.
    assert h3.latlng_to_cell(clat, clon, res) == h3.int_to_str(cell_int)
    # And is near the requested point (res-5 cells are ~20 km across).
    assert abs(clat - lat) < 0.3
    assert abs(clon - lon) < 0.3


def test_centroid_matches_h3_reference() -> None:
    cell_int = h3.str_to_int(h3.latlng_to_cell(0.0, 0.0, 5))
    assert cell_centroid(cell_int) == h3.cell_to_latlng(h3.int_to_str(cell_int))
