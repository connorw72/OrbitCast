# M-Lab BigQuery setup (deferred in Phase 2)

M-Lab NDT is the primary throughput+latency label source (CLAUDE.md D7, §4.2a),
filtered to Starlink (client ASN 14593). It is deferred until BigQuery access is
configured, because the access step is not automatable.

## One-time setup
1. Have (or create) a Google account and a GCP project (for the free 1 TB/month
   query quota — M-Lab tables are public, you pay only for your own query bytes).
2. Join the M-Lab Google Group (grants read access to `measurement-lab.*`):
   <https://groups.google.com/g/discuss-measurement-lab>  **[VERIFY the exact
   subscription step at build time]**.
3. Install the gcloud SDK and authenticate:
   `gcloud auth application-default login`.
4. `uv add --package orbitcast-pipelines google-cloud-bigquery`.

## Then
`orbitcast_pipelines.mlab.ingest_mlab_month(client, year, month)` runs the res-4
aggregate query (F2: M-Lab geolocation snaps to PoP cities, so aggregate to res
3-4 only). Wire it into the `mlab_ingest` Dagster job (monthly) — the asset is
already present as `mlab_labels`.

Stay inside the free 1 TB/month quota: select only needed columns and
partition-prune by date.
