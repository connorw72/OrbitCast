"""M-Lab NDT ingest — throughput + latency labels (CLAUDE.md D7, §4.2a).

M-Lab NDT7 is the primary throughput+latency source, filtered to Starlink
(client ASN 14593). Access is BigQuery `measurement-lab.ndt.unified_downloads` /
`unified_uploads` (free, 1 TB/mo), which requires a Google account joined to the
M-Lab Google Group — the credentialed step is not automatable (see
docs/mlab-setup.md), so `ingest_mlab_month` takes an already-authenticated client.
Everything downstream of the query (`aggregate_mlab_to_labels`) is pure and tested.

Known defect to design around (F2): Starlink uses CGNAT; IP geolocation frequently
snaps to the PoP/gateway city. So we aggregate to H3 **res 4** (regional) only,
never res 5; the hierarchical fallback (§6.3) absorbs the imprecision.
"""

import calendar
from collections import defaultdict
from datetime import datetime

import h3

STARLINK_ASN = 14593

# Regional aggregation resolution for M-Lab (F2) — never res 5.
MLAB_RESOLUTION = 4

# Aggregate to res-4 (regional) — never pretend res-5 precision for M-Lab (F2).
# Hour bucketing is on `a.TestTime` (the TIMESTAMP of the test); `date` is the
# DATE partition column and is used only in WHERE for partition pruning — you
# cannot TIMESTAMP_TRUNC a DATE to HOUR.
QUERY_TEMPLATE = """
SELECT
  TIMESTAMP_TRUNC(a.TestTime, HOUR) AS hour_utc,
  client.Geo.Latitude  AS lat,
  client.Geo.Longitude AS lon,
  APPROX_QUANTILES(a.MeanThroughputMbps, 100)[OFFSET(50)] AS dl_mbps_median,
  APPROX_QUANTILES(a.MinRTT, 100)[OFFSET(50)]             AS min_rtt_median,
  COUNT(*) AS samples
FROM `measurement-lab.ndt.unified_downloads`
WHERE client.Network.ASNumber = {asn}
  AND date BETWEEN '{start}' AND '{stop}'
  AND client.Geo.Latitude IS NOT NULL
GROUP BY hour_utc, lat, lon
"""


def query_for_month(
    year: int,
    month: int,
    asn: int = STARLINK_ASN,
    start_day: int = 1,
    stop_day: int | None = None,
) -> str:
    """SQL for one month (default) or an inclusive day-range within it.

    A full month scans ~1.6 TB of the NDT table, over BigQuery's free 1 TB/month
    quota (the ASN filter does not reduce bytes scanned). Pass a narrower
    ``start_day``/``stop_day`` window to stay under the free tier; the
    hierarchical fallback (§6.3) tolerates the reduced coverage.
    """
    last_day = calendar.monthrange(year, month)[1]
    stop_day = last_day if stop_day is None else stop_day
    if not (1 <= start_day <= stop_day <= last_day):
        raise ValueError(
            f"invalid day range {start_day}..{stop_day} for {year}-{month:02d} "
            f"(1..{last_day})"
        )
    start = f"{year}-{month:02d}-{start_day:02d}"
    stop = f"{year}-{month:02d}-{stop_day:02d}"
    return QUERY_TEMPLATE.format(asn=asn, start=start, stop=stop)


def ingest_mlab_month(
    client,
    year: int,
    month: int,
    start_day: int = 1,
    stop_day: int | None = None,
) -> list[dict]:
    """Run the monthly extract. `client` is a google.cloud.bigquery.Client.

    Pass ``start_day``/``stop_day`` to ingest a day-range instead of the whole
    month (see `query_for_month` for the free-tier rationale). Returns the raw
    per (hour, lat, lon) rows; feed them to `aggregate_mlab_to_labels` for the
    res-4 (cell, hour) labels.
    """
    sql = query_for_month(year, month, start_day=start_day, stop_day=stop_day)
    rows = client.query(sql).result()
    return [dict(r) for r in rows]


def _floor_hour(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def aggregate_mlab_to_labels(rows: list[dict]) -> list[dict]:
    """Fold raw M-Lab rows into sample-weighted (res-4 cell, hour) label rows.

    Each output row carries a throughput median (`dl_mbps_median`) and a latency
    median (`rtt_ms_median`), each a sample-weighted mean of the per-point medians
    that landed in the cell-hour. Throughput and latency are weighted independently
    so a point missing one metric still contributes the other; a metric with no
    samples in a cell-hour is left ``None``. Rows without coordinates or with no
    samples are dropped (F2).
    """
    buckets: dict[tuple[int, datetime], dict[str, float]] = defaultdict(
        lambda: {"dl_num": 0.0, "dl_w": 0.0, "rtt_num": 0.0, "rtt_w": 0.0, "samples": 0.0}
    )
    for r in rows:
        lat, lon = r.get("lat"), r.get("lon")
        n = int(r.get("samples") or 0)
        if lat is None or lon is None or n <= 0:
            continue
        cell = h3.str_to_int(h3.latlng_to_cell(lat, lon, MLAB_RESOLUTION))
        b = buckets[(cell, _floor_hour(r["hour_utc"]))]
        b["samples"] += n
        dl = r.get("dl_mbps_median")
        if dl is not None:
            b["dl_num"] += float(dl) * n
            b["dl_w"] += n
        rtt = r.get("min_rtt_median")
        if rtt is not None:
            b["rtt_num"] += float(rtt) * n
            b["rtt_w"] += n

    out: list[dict] = []
    for (cell, hour), b in buckets.items():
        out.append(
            {
                "h3_cell": cell,
                "hour_utc": hour,
                "dl_mbps_median": b["dl_num"] / b["dl_w"] if b["dl_w"] else None,
                "rtt_ms_median": b["rtt_num"] / b["rtt_w"] if b["rtt_w"] else None,
                "samples": int(b["samples"]),
            }
        )
    out.sort(key=lambda r: (r["h3_cell"], r["hour_utc"]))
    return out
