# M-Lab BigQuery setup — throughput + latency labels

M-Lab NDT is the primary throughput+latency label source (CLAUDE.md D7, §4.2a),
filtered to Starlink (client ASN 14593). The ingest **code is complete and tested**
(`orbitcast_pipelines.mlab`); the only remaining step is credentialed BigQuery
access, which is not automatable. Once the labels land, the training pipeline trains
the `dl_throughput` booster automatically (it trains any target that has labels) —
which is what populates the download-throughput forecast and the `/v1/map?metric=dl_q50`
hex map.

## One-time setup
1. Have (or create) a Google account and a GCP project — this is only for the free
   1 TB/month query quota. M-Lab tables are public; you pay only for your own query
   bytes. **Cost caveat:** the `client.Network.ASNumber = 14593` filter does *not*
   reduce bytes scanned — BigQuery reads those columns across the whole NDT table for
   the date range, then filters. A **full month scans ~1.6 TB** (over the free 1 TB
   quota, ~$3.68 overage at $6.25/TB). To stay free, ingest a **~14-day window**
   (~0.74 TB) via the optional day-range args below; the hierarchical fallback (§6.3)
   absorbs the reduced coverage.
2. Join the M-Lab Google Group (grants read access to `measurement-lab.*`):
   <https://groups.google.com/g/discuss-measurement-lab>  **[VERIFY the exact
   subscription step at build time]**.
3. Install the gcloud SDK and authenticate with Application Default Credentials:
   `gcloud auth application-default login`.
4. Set your billing/quota project:
   `gcloud auth application-default set-quota-project <YOUR_GCP_PROJECT_ID>`.
5. Add the BigQuery client to the pipelines package:
   `uv add --package orbitcast-pipelines google-cloud-bigquery`.

## Run it (one command)
Ingest and retrain in one shot. Prefer the day-range form to stay under the free
1 TB quota (full-month form scans ~1.6 TB and incurs a small overage charge):

```
# free tier: ~14 days, ~0.74 TB
uv run --package orbitcast-pipelines python scripts/ingest_mlab.py 2026 6 1 14

# full month (~1.6 TB, ~$3.68 over free quota — needs billing enabled)
uv run --package orbitcast-pipelines python scripts/ingest_mlab.py 2026 6
```

This will:
1. Query `measurement-lab.ndt.unified_downloads` for ASN 14593 in that month
   (`orbitcast_pipelines.mlab.query_for_month`).
2. Fold the rows into res-4 `(cell, hour)` labels — throughput **and** minRTT,
   sample-weighted (`aggregate_mlab_to_labels`). Res 4 only, never res 5: Starlink
   CGNAT geolocation snaps to PoP cities (F2); the hierarchical fallback (§6.3)
   absorbs the imprecision.
3. Write `data/marts/mlab_throughput_hourly.parquet`.
4. Run the promotion-gated training. Expect the report to now list **both**
   `latency` and `dl_throughput` targets, each with its own coverage / q50-MAE /
   beats-persistence gate.

Backfill several months by looping the command over `(year, month)` pairs; each run
overwrites the mart, so accumulate months into one mart if you want more history
(or extend the script to append).

## Verify it worked
- `data/evals/<newest>.json` has a `targets.dl_throughput` block with `promote: true`.
- `curl 'localhost:8000/v1/forecast?cell=<cell>'` returns non-null `dl` bands.
- `curl 'localhost:8000/v1/map?metric=dl_q50'` returns non-empty `cells` (the hex
  map was empty before because no throughput model existed).

## Quota hygiene
Stay inside the free 1 TB/month quota: the query selects only the needed columns and
partition-prunes by `date`. One Starlink-only month is a small scan; avoid
`SELECT *` and unbounded date ranges.
