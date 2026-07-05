"""Cold-start-proof hierarchical fallback (CLAUDE.md §6.3).

The serving-time "rolling cell median" feature resolves the best available level:
res-5 cell (>= N labeled hours) -> res-4 parent -> res-3 parent -> latitude-band
global prior. The chosen level is returned as a `basis` tag so the API/UI can be
honest about whether an answer rests on cell, regional, or prior data.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from math import floor

import h3
from orbitcast_core.spatial import cell_centroid

# Latitude-band width (degrees) for the global prior of last resort. Constellation
# density varies strongly with latitude (§6.2), so the prior is banded by it.
BAND_WIDTH_DEG = 10.0

# Basis labels surfaced to the client (§6.3, §7.4 methodology page).
Basis = str  # "cell" | "region" | "latitude_prior"


@dataclass(frozen=True)
class CellStat:
    """Rolling label summary for one H3 cell: the target median and how many
    distinct labeled hours back it."""

    median: float
    hours: int


def latitude_band(lat: float, band_width: float = BAND_WIDTH_DEG) -> int:
    """Integer band index for a latitude (floor division by band width)."""
    return int(floor(lat / band_width))


def resolve_cell_median(
    cell: int,
    lookup: Mapping[int, CellStat],
    lat_prior: Mapping[int, float],
    min_hours: int,
    band_width: float = BAND_WIDTH_DEG,
) -> tuple[float, Basis]:
    """Resolve the rolling-median feature for ``cell`` and report its basis.

    A level qualifies only if it carries at least ``min_hours`` labeled hours.
    res-5 answers as ``"cell"``; res-4/res-3 parents answer as ``"region"``; the
    latitude-band prior answers as ``"latitude_prior"``. Raises ``KeyError`` if no
    prior exists for the cell's latitude band — every reachable band must have one.
    """
    hex5 = h3.int_to_str(cell)

    res5 = lookup.get(cell)
    if res5 is not None and res5.hours >= min_hours:
        return res5.median, "cell"

    for res in (4, 3):
        parent = h3.str_to_int(h3.cell_to_parent(hex5, res))
        stat = lookup.get(parent)
        if stat is not None and stat.hours >= min_hours:
            return stat.median, "region"

    lat, _lon = cell_centroid(cell)
    return lat_prior[latitude_band(lat, band_width)], "latitude_prior"
