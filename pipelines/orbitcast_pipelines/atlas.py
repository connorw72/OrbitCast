"""RIPE Atlas ingest: Starlink probe latency anchors (CLAUDE.md §4.2b).

Enumerate probes on AS14593, read their public builtin ping results (no credits),
map each probe to its H3 res-5 cell, and aggregate min-RTT to an hourly median per
(cell, hour). Only reads existing public results — v1 never schedules new
measurements.
"""

import statistics
from collections.abc import Callable
from datetime import UTC, datetime

import h3
from orbitcast_core.spatial import RESOLUTION

STARLINK_ASN = 14593
_PROBES_URL = "https://atlas.ripe.net/api/v2/probes/"
_RESULTS_URL = "https://atlas.ripe.net/api/v2/measurements/{msm_id}/results/"


def probes_to_cells(probes: list[dict]) -> dict[int, int]:
    """Map probe id -> H3 res-5 BIGINT cell, dropping probes without coordinates.

    The RIPE Atlas API returns probe coordinates as GeoJSON
    ``geometry: {"type": "Point", "coordinates": [lon, lat]}``.
    """
    cells: dict[int, int] = {}
    for probe in probes:
        coords = (probe.get("geometry") or {}).get("coordinates")
        if not coords:
            continue
        lon, lat = coords[0], coords[1]
        cells[probe["id"]] = h3.str_to_int(h3.latlng_to_cell(lat, lon, RESOLUTION))
    return cells


def find_ping_measurement(
    probe_id: int,
    fetch: Callable[[str, dict], dict] | None = None,
) -> int | None:
    """First ongoing public ping measurement a probe participates in, or None."""
    fetch = fetch or _http_get_json
    page = fetch(
        "https://atlas.ripe.net/api/v2/measurements/",
        {
            "current_probes": probe_id,
            "type": "ping",
            "status": 2,
            "is_public": "true",
            "page_size": 1,
        },
    )
    results = page.get("results", [])
    return results[0]["id"] if results else None


def aggregate_pings_to_hourly(results: list[dict], probe_cells: dict[int, int]) -> list[dict]:
    """Aggregate ping results to an hourly median RTT per (cell, hour).

    Skips probes not in `probe_cells` and packet-loss samples (min < 0).
    """
    buckets: dict[tuple[int, datetime], list[float]] = {}
    for r in results:
        pid = r.get("prb_id")
        cell = probe_cells.get(pid) if isinstance(pid, int) else None
        rtt = r.get("min")
        if cell is None or rtt is None or rtt < 0:
            continue
        hour = datetime.fromtimestamp(r["timestamp"], UTC).replace(
            minute=0, second=0, microsecond=0
        )
        buckets.setdefault((cell, hour), []).append(float(rtt))
    return [
        {
            "h3_cell": cell,
            "hour_utc": hour,
            "rtt_ms_median": statistics.median(samples),
            "samples": len(samples),
        }
        for (cell, hour), samples in buckets.items()
    ]


# --- network wrappers (injected in tests; hit the live API in the backfill) ---


def _http_get_json(url: str, params: dict) -> dict:
    import httpx

    resp = httpx.get(url, params=params, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def enumerate_probes(
    asn: int = STARLINK_ASN,
    fetch: Callable[[str, dict], dict] = _http_get_json,
) -> list[dict]:
    """List connected probes on `asn` (paginated)."""
    probes: list[dict] = []
    params = {"asn_v4": asn, "status": 1, "page_size": 500}
    url: str | None = _PROBES_URL
    while url:
        page = fetch(url, params)
        probes.extend(page.get("results", []))
        url = page.get("next")
        params = {}  # `next` already carries the query
    return probes


def fetch_ping_results(
    msm_id: int,
    start: int,
    stop: int,
    probe_ids: list[int] | None = None,
    fetch: Callable[[str, dict], dict] | None = None,
) -> list[dict]:
    """Fetch public results of ping measurement `msm_id` in [start, stop],
    optionally filtered to `probe_ids`."""
    fetch = fetch or _http_get_json
    params: dict = {"start": start, "stop": stop, "format": "json"}
    if probe_ids:
        params["probe_ids"] = ",".join(str(p) for p in probe_ids)
    data = fetch(_RESULTS_URL.format(msm_id=msm_id), params)
    return data if isinstance(data, list) else data.get("results", [])
