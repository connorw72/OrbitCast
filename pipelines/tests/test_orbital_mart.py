"""Orbital feature mart (CLAUDE.md §4.1, §5.5).

Hourly supply proxies (sats_visible, max_elevation_deg) computed only at the
(cell, hour) pairs present in the labels — never a dense global grid (§5.3).
"""

from datetime import UTC, datetime

import h3
from orbitcast_pipelines import warehouse
from orbitcast_pipelines.orbital_mart import build_orbital_features, label_cell_hours

_CELL = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 5))
_H = datetime(2026, 7, 6, 12, tzinfo=UTC)


def test_label_cell_hours_returns_distinct_pairs(tmp_path):
    marts = tmp_path / "marts"
    marts.mkdir()
    warehouse.write_mart(
        [
            {"h3_cell": _CELL, "hour_utc": _H, "rtt_ms_median": 40.0, "samples": 3},
            {"h3_cell": _CELL, "hour_utc": _H, "rtt_ms_median": 41.0, "samples": 3},
            {
                "h3_cell": _CELL,
                "hour_utc": datetime(2026, 7, 6, 13, tzinfo=UTC),
                "rtt_ms_median": 42.0,
                "samples": 3,
            },
        ],
        marts / "atlas_latency_hourly.parquet",
    )
    pairs = label_cell_hours(marts)
    assert len(pairs) == 2
    assert (_CELL, _H) in pairs


def test_label_cell_hours_empty_when_no_mart(tmp_path):
    assert label_cell_hours(tmp_path / "marts") == []


def test_build_orbital_features_shapes_rows_per_pair():
    # No satellites -> zero visible, no best elevation; still one row per pair.
    rows = build_orbital_features([], [(_CELL, _H)])
    assert len(rows) == 1
    r = rows[0]
    assert r["h3_cell"] == _CELL
    assert r["hour_utc"] == _H
    assert r["sats_visible"] == 0
    assert r["max_elevation_deg"] is None
