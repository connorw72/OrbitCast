"""Ookla quadkey -> H3 res-5 aggregation (CLAUDE.md §4.2d).

Ookla is all-providers fixed broadband per tile (no ISP breakdown, F1). Its
legitimate use here is a `terrestrial_baseline_mbps` context feature and a demand
proxy (`devices`), aggregated from zoom-16 tiles to H3 res-5, test-weighted.
Tested on a local table so CI never touches the network.
"""

import duckdb
import h3
from orbitcast_pipelines.ookla import aggregate_ookla_to_h3
from orbitcast_pipelines.warehouse import load_extensions


def _square(lat: float, lon: float, d: float = 0.001) -> str:
    """A tiny WKT square centered on (lat, lon); its centroid is (lat, lon)."""
    return (
        f"POLYGON(({lon - d} {lat - d}, {lon + d} {lat - d}, "
        f"{lon + d} {lat + d}, {lon - d} {lat + d}, {lon - d} {lat - d}))"
    )


# (quadkey, lat, lon, avg_d_kbps, tests, devices)
_ROWS = [
    ("a", 47.600, -122.330, 100_000, 10, 5),
    ("b", 47.605, -122.335, 200_000, 30, 10),  # near a
    ("c", 40.000, -105.000, 50_000, 5, 2),  # far away
]


def _make_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "CREATE TABLE ookla(quadkey VARCHAR, tile VARCHAR, avg_d_kbps BIGINT, "
        "avg_u_kbps BIGINT, avg_lat_ms BIGINT, tests BIGINT, devices BIGINT)"
    )
    for qk, lat, lon, d_kbps, tests, devices in _ROWS:
        con.execute(
            "INSERT INTO ookla VALUES (?,?,?,?,?,?,?)",
            [qk, _square(lat, lon), d_kbps, d_kbps // 5, 30, tests, devices],
        )


def test_aggregates_tiles_into_h3_cells_test_weighted() -> None:
    con = duckdb.connect()
    load_extensions(con)
    _make_table(con)

    result = {row["h3_cell"]: row for row in aggregate_ookla_to_h3(con, "ookla")}

    # Expected grouping computed independently via the h3 library.
    expected: dict[int, dict] = {}
    for _qk, lat, lon, d_kbps, tests, devices in _ROWS:
        cell = h3.str_to_int(h3.latlng_to_cell(lat, lon, 5))
        agg = expected.setdefault(cell, {"tests": 0, "devices": 0, "wsum": 0.0})
        agg["tests"] += tests
        agg["devices"] += devices
        agg["wsum"] += d_kbps * tests

    assert set(result) == set(expected)
    for cell, exp in expected.items():
        assert result[cell]["tests"] == exp["tests"]
        assert result[cell]["devices"] == exp["devices"]
        expected_mbps = exp["wsum"] / exp["tests"] / 1000.0
        assert abs(result[cell]["terrestrial_baseline_mbps"] - expected_mbps) < 1e-6
