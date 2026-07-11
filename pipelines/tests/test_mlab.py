"""M-Lab NDT ingest transform (CLAUDE.md D7, §4.2a, F2).

The BigQuery query returns per (hour, client-lat, client-lon) medians. The
transform maps those to H3 res-4 cells (never res-5 — Starlink CGNAT geo snaps to
PoP cities, F2) and combines them, sample-weighted, into (cell, hour) label rows
carrying both throughput and minRTT. No network here — the BigQuery client is
mocked; only the pure aggregation is exercised.
"""

from datetime import UTC, datetime

import h3
from orbitcast_pipelines.mlab import (
    aggregate_mlab_to_labels,
    ingest_mlab_month,
    query_for_month,
)

_HOUR = datetime(2026, 6, 15, 20, tzinfo=UTC)
# Two nearby points that fall in the same res-4 cell, plus one far away.
_LAT_A, _LON_A = 52.28, 8.05
_LAT_B, _LON_B = 52.30, 8.10
_LAT_FAR, _LON_FAR = -33.9, 151.2


def _res4(lat: float, lon: float) -> int:
    return h3.str_to_int(h3.latlng_to_cell(lat, lon, 4))


def test_query_targets_starlink_asn_and_month():
    sql = query_for_month(2026, 6)
    assert "14593" in sql
    assert "2026-06-01" in sql
    assert "unified_downloads" in sql


def test_query_buckets_on_testtime_not_date_partition():
    # `date` is a DATE partition column; you cannot TIMESTAMP_TRUNC it to HOUR.
    # Hour bucketing must be on the a.TestTime TIMESTAMP (regression: BadRequest
    # "TIMESTAMP_TRUNC does not support the HOUR date part").
    sql = query_for_month(2026, 6)
    assert "TIMESTAMP_TRUNC(a.TestTime, HOUR)" in sql
    assert "TIMESTAMP_TRUNC(date" not in sql


def test_query_covers_full_month_including_30th():
    # June has 30 days; the window must not stop short at the 28th.
    assert "2026-06-30" in query_for_month(2026, 6)
    # February 2026 (non-leap) ends on the 28th.
    assert "2026-02-28" in query_for_month(2026, 2)


def test_aggregate_maps_to_res4_and_weights_by_samples():
    rows = [
        {
            "hour_utc": _HOUR,
            "lat": _LAT_A,
            "lon": _LON_A,
            "dl_mbps_median": 100.0,
            "min_rtt_median": 30.0,
            "samples": 3,
        },
        {
            "hour_utc": _HOUR,
            "lat": _LAT_B,
            "lon": _LON_B,
            "dl_mbps_median": 200.0,
            "min_rtt_median": 50.0,
            "samples": 1,
        },
    ]
    out = aggregate_mlab_to_labels(rows)

    assert len(out) == 1
    row = out[0]
    assert row["h3_cell"] == _res4(_LAT_A, _LON_A)
    assert h3.get_resolution(h3.int_to_str(row["h3_cell"])) == 4
    # Sample-weighted mean: (100*3 + 200*1) / 4 = 125.
    assert row["dl_mbps_median"] == 125.0
    assert row["rtt_ms_median"] == (30.0 * 3 + 50.0 * 1) / 4
    assert row["samples"] == 4


def test_aggregate_separates_distinct_cells_and_hours():
    other_hour = datetime(2026, 6, 15, 21, tzinfo=UTC)
    rows = [
        {
            "hour_utc": _HOUR,
            "lat": _LAT_A,
            "lon": _LON_A,
            "dl_mbps_median": 100.0,
            "min_rtt_median": 30.0,
            "samples": 2,
        },
        {
            "hour_utc": other_hour,
            "lat": _LAT_A,
            "lon": _LON_A,
            "dl_mbps_median": 120.0,
            "min_rtt_median": 33.0,
            "samples": 2,
        },
        {
            "hour_utc": _HOUR,
            "lat": _LAT_FAR,
            "lon": _LON_FAR,
            "dl_mbps_median": 80.0,
            "min_rtt_median": 40.0,
            "samples": 5,
        },
    ]
    out = aggregate_mlab_to_labels(rows)
    assert len(out) == 3
    keys = {(r["h3_cell"], r["hour_utc"]) for r in out}
    assert (_res4(_LAT_FAR, _LON_FAR), _HOUR) in keys


def test_aggregate_skips_missing_geo_and_zero_samples():
    rows = [
        {
            "hour_utc": _HOUR,
            "lat": None,
            "lon": None,
            "dl_mbps_median": 100.0,
            "min_rtt_median": 30.0,
            "samples": 4,
        },
        {
            "hour_utc": _HOUR,
            "lat": _LAT_A,
            "lon": _LON_A,
            "dl_mbps_median": 100.0,
            "min_rtt_median": 30.0,
            "samples": 0,
        },
    ]
    assert aggregate_mlab_to_labels(rows) == []


def test_aggregate_handles_missing_metric_independently():
    # A cell-hour with throughput but no RTT still yields a throughput label.
    rows = [
        {
            "hour_utc": _HOUR,
            "lat": _LAT_A,
            "lon": _LON_A,
            "dl_mbps_median": 100.0,
            "min_rtt_median": None,
            "samples": 4,
        },
    ]
    (row,) = aggregate_mlab_to_labels(rows)
    assert row["dl_mbps_median"] == 100.0
    assert row["rtt_ms_median"] is None


class _FakeQueryJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return self._rows


class _FakeBQClient:
    def __init__(self, rows):
        self._rows = rows
        self.last_sql = None

    def query(self, sql):
        self.last_sql = sql
        return _FakeQueryJob(self._rows)


def test_ingest_mlab_month_runs_query_and_returns_dicts():
    raw = [
        {
            "hour_utc": _HOUR,
            "lat": _LAT_A,
            "lon": _LON_A,
            "dl_mbps_median": 100.0,
            "min_rtt_median": 30.0,
            "samples": 3,
        }
    ]
    client = _FakeBQClient(raw)
    out = ingest_mlab_month(client, 2026, 6)
    assert client.last_sql is not None and "14593" in client.last_sql
    assert out == raw
