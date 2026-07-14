"""Fallback-mart builder (CLAUDE.md §6.3): cell_label_stats + latitude_priors.

These two marts are what the serving-side hierarchical fallback resolves against;
without them every request answers as latitude_prior with a NaN median. The stats
mart carries the rolling 7-day latency median per cell — anchored at each cell's
*own* last labeled hour, mirroring the training feature's trailing window — plus
res-4/res-3 parent roll-ups so `resolve_cell_median` can walk up the hierarchy.
The priors mart is the 10-degree-band global median of last resort.
"""

from datetime import UTC, datetime, timedelta

import duckdb
import h3
import pytest
from orbitcast_ml.fallback import CellStat, latitude_band, resolve_cell_median
from orbitcast_pipelines import warehouse
from orbitcast_pipelines.fallback_marts import build_fallback_marts

_RES5 = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 5))
_RES4 = h3.str_to_int(h3.cell_to_parent(h3.int_to_str(_RES5), 4))
_RES3 = h3.str_to_int(h3.cell_to_parent(h3.int_to_str(_RES5), 3))
_T0 = datetime(2026, 7, 1, 0, tzinfo=UTC)


@pytest.fixture
def con():
    c = duckdb.connect()
    warehouse.load_extensions(c)
    c.execute("SET TimeZone='UTC';")
    return c


def _atlas_rows(cell: int, start: datetime, n_hours: int, rtt: float = 40.0) -> list[dict]:
    return [
        {
            "h3_cell": cell,
            "hour_utc": start + timedelta(hours=i),
            "rtt_ms_median": rtt,
            "samples": 3,
        }
        for i in range(n_hours)
    ]


def _read(marts, name):
    return warehouse.read_mart(marts / name)


def test_stats_mart_carries_median_hours_and_parent_rollups(con, tmp_path):
    marts = tmp_path
    warehouse.write_mart(
        _atlas_rows(_RES5, _T0, 10, rtt=40.0), marts / "atlas_latency_hourly.parquet"
    )

    n_stats, n_priors = build_fallback_marts(con, marts)
    assert n_stats >= 3 and n_priors == 1

    stats = {int(r["h3_cell"]): r for r in _read(marts, "cell_label_stats.parquet")}
    # res-5 cell plus both parent levels, all with the same 10 labeled hours here.
    for cell in (_RES5, _RES4, _RES3):
        assert stats[cell]["median"] == 40.0
        assert stats[cell]["hours"] == 10

    priors = {int(r["band"]): float(r["median"]) for r in _read(marts, "latitude_priors.parquet")}
    lat, _lon = h3.cell_to_latlng(h3.int_to_str(_RES5))
    assert priors[latitude_band(lat)] == 40.0


def test_window_anchors_at_each_cells_own_last_hour(con, tmp_path):
    # Old labels (well beyond 7 days before the cell's last hour) must not enter
    # the median; the window is the cell's own most recent 7 labeled days.
    marts = tmp_path
    old = _atlas_rows(_RES5, _T0 - timedelta(days=30), 24, rtt=999.0)
    recent = _atlas_rows(_RES5, _T0, 24, rtt=40.0)
    warehouse.write_mart(old + recent, marts / "atlas_latency_hourly.parquet")

    build_fallback_marts(con, marts)
    stats = {int(r["h3_cell"]): r for r in _read(marts, "cell_label_stats.parquet")}
    assert stats[_RES5]["median"] == 40.0
    assert stats[_RES5]["hours"] == 24


def test_mlab_res4_rows_enter_at_region_level_not_res5(con, tmp_path):
    marts = tmp_path
    mlab = [
        {
            "h3_cell": _RES4,
            "hour_utc": _T0 + timedelta(hours=i),
            "dl_mbps_median": 100.0,
            "rtt_ms_median": 60.0,
            "samples": 5,
        }
        for i in range(6)
    ]
    warehouse.write_mart(mlab, marts / "mlab_throughput_hourly.parquet")

    build_fallback_marts(con, marts)
    stats = {int(r["h3_cell"]): r for r in _read(marts, "cell_label_stats.parquet")}
    assert _RES5 not in stats  # M-Lab geolocation never pretends res-5 precision (F2)
    assert stats[_RES4]["median"] == 60.0
    assert stats[_RES3]["median"] == 60.0


def test_marts_resolve_through_the_serving_fallback(con, tmp_path):
    # End-to-end against the real resolver: a cell with >= min_hours answers as
    # "cell"; a sibling res-5 cell under the same parent answers as "region".
    marts = tmp_path
    warehouse.write_mart(
        _atlas_rows(_RES5, _T0, 168, rtt=35.0), marts / "atlas_latency_hourly.parquet"
    )
    build_fallback_marts(con, marts)

    lookup = {
        int(r["h3_cell"]): CellStat(median=float(r["median"]), hours=int(r["hours"]))
        for r in _read(marts, "cell_label_stats.parquet")
    }
    priors = {int(r["band"]): float(r["median"]) for r in _read(marts, "latitude_priors.parquet")}

    assert resolve_cell_median(_RES5, lookup, priors, min_hours=168) == (35.0, "cell")
    sibling = next(
        h3.str_to_int(c)
        for c in h3.cell_to_children(h3.int_to_str(_RES4), 5)
        if h3.str_to_int(c) != _RES5
    )
    assert resolve_cell_median(sibling, lookup, priors, min_hours=168) == (35.0, "region")


def test_no_label_marts_builds_nothing(con, tmp_path):
    assert build_fallback_marts(con, tmp_path) == (0, 0)
    assert not (tmp_path / "cell_label_stats.parquet").exists()
    assert not (tmp_path / "latitude_priors.parquet").exists()


def test_user_measurements_contribute_latency_only(con, tmp_path):
    marts = tmp_path
    rows = [
        {
            "h3_cell": _RES5,
            "hour_utc": _T0 + timedelta(hours=i),
            "target": target,
            "value_median": value,
            "samples": 4,
        }
        for i in range(3)
        for target, value in (("latency", 30.0), ("dl_throughput", 120.0))
    ]
    warehouse.write_mart(rows, marts / "user_measurements_hourly.parquet")

    build_fallback_marts(con, marts)
    stats = {int(r["h3_cell"]): r for r in _read(marts, "cell_label_stats.parquet")}
    assert stats[_RES5]["median"] == 30.0  # throughput rows never pollute the latency median
