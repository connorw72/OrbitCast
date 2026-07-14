"""forecast_cache write-through (CLAUDE.md §7.2, design doc Part 1b).

Integration tests against dockerized Postgres: upserted quantile bands round-trip,
reads are scoped to the promoted model version (superseded versions are simply
never matched), and re-upserting the same (cell, hour, metric) overwrites in place
so the table stays bounded at active-cell scale.
"""

from datetime import UTC, datetime, timedelta

import h3
import pytest
from orbitcast_api import db
from orbitcast_api.forecast_cache import select_bands, upsert_bands

_CELL = h3.str_to_int(h3.latlng_to_cell(52.28, 8.05, 5))
_H0 = datetime(2026, 7, 13, 12, tzinfo=UTC)


def _entry(hour: datetime, latency_q50: float = 30.0, dl=None, basis: str = "region") -> dict:
    return {
        "hour": hour.isoformat(),
        "basis": basis,
        "latency": {"q10": 20.0, "q50": latency_q50, "q90": 45.0},
        "dl": dl,
        "weather": {"precip_mm_h": 0.0},
    }


@pytest.fixture
def conn(db_pool):
    with db.get_pool().connection() as c:
        yield c


def test_upsert_then_select_roundtrips_bands_and_basis(conn):
    dl = {"q10": 80.0, "q50": 120.0, "q90": 150.0}
    upsert_bands(conn, _CELL, "v1", [_entry(_H0, dl=dl)])

    cached = select_bands(conn, _CELL, [_H0], "v1")
    assert set(cached) == {_H0}
    assert cached[_H0]["basis"] == "region"
    assert cached[_H0]["latency"] == {"q10": 20.0, "q50": 30.0, "q90": 45.0}
    assert cached[_H0]["dl"] == dl


def test_select_ignores_other_versions_hours_and_cells(conn):
    upsert_bands(conn, _CELL, "v1", [_entry(_H0)])

    assert select_bands(conn, _CELL, [_H0], "v2") == {}
    assert select_bands(conn, _CELL, [_H0 + timedelta(hours=1)], "v1") == {}
    other = h3.str_to_int(h3.latlng_to_cell(-33.9, 151.2, 5))
    assert select_bands(conn, other, [_H0], "v1") == {}


def test_upsert_overwrites_in_place_for_new_version(conn):
    upsert_bands(conn, _CELL, "v1", [_entry(_H0, latency_q50=30.0)])
    upsert_bands(conn, _CELL, "v2", [_entry(_H0, latency_q50=33.0)])

    assert select_bands(conn, _CELL, [_H0], "v1") == {}  # superseded, never matched
    assert select_bands(conn, _CELL, [_H0], "v2")[_H0]["latency"]["q50"] == 33.0
    n = conn.execute(
        "SELECT count(*) FROM forecast_cache WHERE h3_cell = %s AND metric = 'latency'",
        (_CELL,),
    ).fetchone()[0]
    assert n == 1  # overwritten, not accumulated


def test_absent_metric_reconstructs_as_none(conn):
    # Throughput can be unlabeled (pre-M-Lab): no dl row is written, and the
    # cached hour must come back with dl=None rather than a fabricated band.
    upsert_bands(conn, _CELL, "v1", [_entry(_H0, dl=None)])
    cached = select_bands(conn, _CELL, [_H0], "v1")
    assert cached[_H0]["dl"] is None
    assert cached[_H0]["latency"] is not None


def test_select_covers_only_requested_hours(conn):
    hours = [_H0 + timedelta(hours=i) for i in range(4)]
    upsert_bands(conn, _CELL, "v1", [_entry(h) for h in hours[:2]])
    cached = select_bands(conn, _CELL, hours, "v1")
    assert set(cached) == set(hours[:2])
