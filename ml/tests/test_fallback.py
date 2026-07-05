"""Hierarchical fallback resolution (CLAUDE.md §6.3).

Every prediction resolves the best available level:
res-5 cell (>= N labeled hours) -> res-4 parent -> res-3 parent -> latitude-band
global prior. The resolved level is reported as `basis` so the UI can say "based
on regional data" honestly. This single mechanism makes the product usable
everywhere on day one while sharpening wherever users cluster.
"""

import h3
import pytest
from orbitcast_ml.fallback import CellStat, resolve_cell_median

# A concrete res-5 cell and its ancestors (Osnabrueck-ish, a WetLinks site).
_RES5 = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 5))
_RES4 = h3.str_to_int(h3.cell_to_parent(h3.int_to_str(_RES5), 4))
_RES3 = h3.str_to_int(h3.cell_to_parent(h3.int_to_str(_RES5), 3))
_LAT_PRIOR = {5: 40.0}  # band index floor(52.28/10)=5 -> prior median 40.0


def test_cell_level_when_res5_has_enough_hours():
    lookup = {_RES5: CellStat(median=25.0, hours=200)}
    value, basis = resolve_cell_median(_RES5, lookup, _LAT_PRIOR, min_hours=168)
    assert value == 25.0
    assert basis == "cell"


def test_falls_back_to_res4_region_when_res5_thin():
    lookup = {
        _RES5: CellStat(median=25.0, hours=10),  # below threshold
        _RES4: CellStat(median=30.0, hours=500),
    }
    value, basis = resolve_cell_median(_RES5, lookup, _LAT_PRIOR, min_hours=168)
    assert value == 30.0
    assert basis == "region"


def test_falls_back_to_res3_region():
    lookup = {_RES3: CellStat(median=33.0, hours=900)}
    value, basis = resolve_cell_median(_RES5, lookup, _LAT_PRIOR, min_hours=168)
    assert value == 33.0
    assert basis == "region"


def test_falls_back_to_latitude_prior_when_nothing_local():
    value, basis = resolve_cell_median(_RES5, {}, _LAT_PRIOR, min_hours=168)
    assert value == 40.0
    assert basis == "latitude_prior"


def test_res5_present_but_below_threshold_is_not_cell_basis():
    lookup = {_RES5: CellStat(median=25.0, hours=167)}  # one short
    value, basis = resolve_cell_median(_RES5, lookup, _LAT_PRIOR, min_hours=168)
    assert basis == "latitude_prior"
    assert value == 40.0


def test_missing_latitude_band_raises():
    # A prior must exist for every band the globe can produce; a gap is a bug.
    with pytest.raises(KeyError):
        resolve_cell_median(_RES5, {}, {}, min_hours=168)
