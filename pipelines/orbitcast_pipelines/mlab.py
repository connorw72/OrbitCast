"""M-Lab NDT ingest — SCAFFOLD, deferred (CLAUDE.md D7, §4.2a).

M-Lab NDT7 is the primary throughput+latency source, filtered to Starlink
(client ASN 14593). Access is BigQuery `measurement-lab.ndt.unified_downloads` /
`unified_uploads` (free, 1 TB/mo), which requires a Google account joined to the
M-Lab Google Group. That setup is not automatable, so this module is wired but not
run until credentials exist. See docs/mlab-setup.md.

Known defect to design around (F2): Starlink uses CGNAT; IP geolocation frequently
snaps to the PoP/gateway city. Aggregate to H3 res 3-4 (regional) only; the
hierarchical fallback (§6.3) absorbs the imprecision.
"""

STARLINK_ASN = 14593

# Aggregate to res-4 (regional) — never pretend res-5 precision for M-Lab (F2).
QUERY_TEMPLATE = """
SELECT
  TIMESTAMP_TRUNC(date, HOUR) AS hour_utc,
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


def query_for_month(year: int, month: int, asn: int = STARLINK_ASN) -> str:
    start = f"{year}-{month:02d}-01"
    stop = f"{year}-{month:02d}-28"
    return QUERY_TEMPLATE.format(asn=asn, start=start, stop=stop)


def ingest_mlab_month(client, year: int, month: int) -> list[dict]:  # pragma: no cover
    """Run the monthly extract. `client` is a google.cloud.bigquery.Client.
    Deferred: requires BigQuery credentials (see module docstring)."""
    rows = client.query(query_for_month(year, month)).result()
    return [dict(r) for r in rows]
