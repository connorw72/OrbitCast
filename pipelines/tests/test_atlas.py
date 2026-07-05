"""RIPE Atlas latency anchors: probe -> res-5 cell, hourly RTT (CLAUDE.md §4.2b).

~99 probes on AS14593; probe coordinates are public (fuzzed <=1 km, fine for res
5). Reading existing public builtin ping results costs no credits. The pure fusion
logic (cell mapping + hourly aggregation, loss-filtered) is tested here; the
network fetch is injected so CI stays offline.
"""

from datetime import UTC, datetime

import h3
from orbitcast_pipelines.atlas import aggregate_pings_to_hourly, probes_to_cells


def _geo(lon: float, lat: float) -> dict:
    # RIPE Atlas returns coordinates as GeoJSON [lon, lat].
    return {"type": "Point", "coordinates": [lon, lat]}


def test_probes_map_to_res5_cells() -> None:
    probes = [
        {"id": 1, "geometry": _geo(-122.3, 47.6)},
        {"id": 2, "geometry": _geo(-105.0, 40.0)},
        {"id": 3, "geometry": None},  # no coords -> dropped
    ]
    cells = probes_to_cells(probes)
    assert cells[1] == h3.str_to_int(h3.latlng_to_cell(47.6, -122.3, 5))
    assert cells[2] == h3.str_to_int(h3.latlng_to_cell(40.0, -105.0, 5))
    assert 3 not in cells


def test_aggregate_pings_hourly_median_with_loss_and_unknown_filtered() -> None:
    probe_cells = {1: 111, 2: 222}
    h12 = int(datetime(2026, 7, 4, 12, 0, tzinfo=UTC).timestamp())
    h13 = int(datetime(2026, 7, 4, 13, 30, tzinfo=UTC).timestamp())
    results = [
        {"prb_id": 1, "timestamp": h12, "min": 20.0},
        {"prb_id": 1, "timestamp": h12 + 10, "min": 30.0},  # same (cell, hour) -> median 25
        {"prb_id": 1, "timestamp": h12 + 20, "min": -1},  # packet loss -> excluded
        {"prb_id": 2, "timestamp": h13, "min": 50.0},  # other cell/hour
        {"prb_id": 99, "timestamp": h12, "min": 15.0},  # unknown probe -> skipped
    ]

    out = {
        (r["h3_cell"], r["hour_utc"]): r for r in aggregate_pings_to_hourly(results, probe_cells)
    }

    key12 = (111, datetime(2026, 7, 4, 12, tzinfo=UTC))
    assert out[key12]["rtt_ms_median"] == 25.0
    assert out[key12]["samples"] == 2
    key13 = (222, datetime(2026, 7, 4, 13, tzinfo=UTC))
    assert out[key13]["rtt_ms_median"] == 50.0
    assert all(cell != 999 for cell, _hour in out)
