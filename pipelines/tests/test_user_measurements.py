"""Crowdsourced measurements -> hourly user-source labels (CLAUDE.md §4.3, §6.2).

The pure aggregation is unit-tested here with plain dicts (mirroring the Atlas
aggregator); the Postgres fetch is a thin wrapper exercised end-to-end elsewhere.
"""

from datetime import UTC, datetime

from orbitcast_pipelines.user_measurements import aggregate_measurements_to_hourly

_CELL = 599686042433355775


def _m(hour: int, minute: int, *, latency=None, dl=None) -> dict:
    return {
        "ts": datetime(2026, 7, 7, hour, minute, tzinfo=UTC),
        "h3_cell": _CELL,
        "latency_ms": latency,
        "dl_mbps": dl,
    }


def test_medians_per_hour_and_target() -> None:
    rows = [
        _m(20, 0, latency=40.0),
        _m(20, 15, latency=50.0, dl=100.0),  # two latency samples in hour 20
        _m(20, 30, dl=120.0),  # two dl samples in hour 20
    ]
    out = {(r["target"]): r for r in aggregate_measurements_to_hourly(rows)}
    assert out["latency"]["value_median"] == 45.0  # median(40, 50)
    assert out["latency"]["samples"] == 2
    assert out["dl_throughput"]["value_median"] == 110.0  # median(100, 120)
    assert out["dl_throughput"]["samples"] == 2
    assert all(r["hour_utc"] == datetime(2026, 7, 7, 20, tzinfo=UTC) for r in out.values())
    assert all(r["h3_cell"] == _CELL for r in out.values())


def test_distinct_hours_are_separate_buckets() -> None:
    rows = [_m(20, 5, latency=40.0), _m(21, 5, latency=60.0)]
    out = aggregate_measurements_to_hourly(rows)
    assert len(out) == 2
    assert sorted(r["value_median"] for r in out) == [40.0, 60.0]


def test_null_readings_are_skipped() -> None:
    # A browser-probe row carries latency only; its NULL dl must not create a
    # dl_throughput label.
    rows = [_m(20, 0, latency=40.0)]
    out = aggregate_measurements_to_hourly(rows)
    assert [r["target"] for r in out] == ["latency"]
