"""Orbital feature mart (CLAUDE.md §4.1, §5.5, §6.2).

Computes the supply-side sky features (sats_visible, max_elevation_deg) at exactly
the (cell, hour) pairs that appear in the labels, so the training matrix has an
orbital row to join. Sparse by construction — never a dense global grid (§5.3).

The features come from the current CelesTrak GP cache propagated to each label
hour. For hours far from the GP epoch the instantaneous count degrades, but as a
coarse, latitude-dominated supply proxy it stays informative (F5).
"""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from orbitcast_core.orbital import sky_view
from orbitcast_core.spatial import cell_centroid
from skyfield.api import EarthSatellite

from . import warehouse


def label_cell_hours(marts_dir: Path) -> list[tuple[int, datetime]]:
    """Distinct (h3_cell, hour_utc) pairs present in the res-5 label marts.

    Unions the RIPE Atlas latency mart and the crowdsourced user-measurements mart
    (both res-5, both carrying h3_cell + hour_utc) so orbital and weather features
    are computed for user cells too. M-Lab joins in once ingested. Returns an empty
    list when no label mart exists yet.
    """
    marts_dir = Path(marts_dir)
    seen: dict[tuple[int, datetime], None] = {}
    for name in ("atlas_latency_hourly.parquet", "user_measurements_hourly.parquet"):
        path = marts_dir / name
        if not path.exists():
            continue
        for row in warehouse.read_mart(path):
            seen.setdefault((row["h3_cell"], row["hour_utc"]), None)
    return sorted(seen)


def build_orbital_features(
    satellites: Sequence[EarthSatellite],
    cell_hours: Sequence[tuple[int, datetime]],
) -> list[dict]:
    """One orbital-feature row per (cell, hour), computed at the cell centroid."""
    rows: list[dict] = []
    for cell, hour in cell_hours:
        lat, lon = cell_centroid(cell)
        view = sky_view(satellites, lat, lon, hour)
        rows.append(
            {
                "h3_cell": cell,
                "hour_utc": hour,
                "sats_visible": view.sats_visible,
                "max_elevation_deg": view.max_elevation_deg,
            }
        )
    return rows
